"""Detached worker process for a persistent Ene session."""

from __future__ import annotations

import json
import os
import queue
import select
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

from ene.backend import LLMAgent
from ene.config import CONFIG_PATH, conf
from ene.hub import discover_hub
from ene.hubclient import HubClient
from ene.live import (
    REQUEST_TIMEOUT,
    TERMINAL_IDLE_TIMEOUT,
    LiveError,
    read_record,
    recv_frame,
    record_path,
    send_frame,
    unlink_record,
    update_identity,
    update_record,
)
from ene.providers import provider_names
from ene.replay import HiddenMessages, compact_replay, hidden_message
from ene.tools.process_manager import format_process_status
from ene.ui import AgentConsole
from ene.utils.io import AgentEvent, CancellationToken, EventHub, InputBroker, PromptBroker


ATTACH_EVENT_QUEUE_SIZE = 1000


def _replay_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select compact authoritative history and mark omissions in place."""
    starts_mid_turn = bool(events and int(events[0].get("seq", 0)) > 1)
    reset_index = next(
        (
            index
            for index in range(len(events) - 1, -1, -1)
            if events[index].get("type") == "timeline_reset"
        ),
        -1,
    )
    events = events[reset_index + 1:]
    visible_types = {
        "user_message", "assistant_message", "system", "warning", "error", "debug",
        "output", "tool_start", "tool_result", "diff", "thinking",
    }
    selected = compact_replay(
        events,
        is_user=lambda event: event.get("type") == "user_message",
        is_assistant=lambda event: event.get("type") == "assistant_message",
        has_text=lambda event: bool(str((event.get("data") or {}).get("text", "")).strip()),
        is_visible=lambda event: event.get("type") in visible_types,
        is_turn_start=lambda event: (
            event.get("type") == "iteration_start"
            and int((event.get("data") or {}).get("iteration", 0)) == 1
        ),
        user_starts_turn=lambda event: (
            (event.get("data") or {}).get("source") == "replay"
        ),
        is_continuation_user=lambda event: bool(
            (event.get("data") or {}).get("steer")
        ),
        starts_mid_turn=starts_mid_turn and reset_index < 0,
    )

    # A snapshot taken during streaming has no consolidated assistant message
    # yet. Preserve its deltas so live deltas can continue from the exact prefix
    # instead of attaching halfway through the response.
    last_assistant_index = next(
        (
            index
            for index in range(len(events) - 1, -1, -1)
            if events[index].get("type") == "assistant_message"
        ),
        -1,
    )
    selected.extend(
        event
        for event in events[last_assistant_index + 1:]
        if event.get("type") in {"assistant_delta", "thinking_delta"}
    )

    replay: list[dict[str, Any]] = []
    for item in selected:
        if isinstance(item, HiddenMessages):
            replay.append({
                "seq": 0,
                "type": "system",
                "data": {"text": hidden_message(item.count), "source": "replay"},
            })
        else:
            replay.append(item)
    return replay


def _active_indicator(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the latest indicator only when it has not subsequently stopped."""
    for event in reversed(events):
        kind = event.get("type")
        if kind == "thinking_stop":
            return None
        if kind == "thinking_start":
            return dict(event.get("data") or {})
    return None


class Worker:
    def __init__(self, record: dict[str, Any]):
        self.record = record
        self.runtime_id = str(record["runtime_id"])
        self.token = str(record["token"])
        self.stop_event = threading.Event()
        self.terminal_lock = threading.Lock()
        self.terminal_attached = False
        self.connections: set[socket.socket] = set()
        self.connections_lock = threading.Lock()
        self.events = EventHub(max_events=10000)
        self.inputs = InputBroker(self.events)
        self.prompts = PromptBroker(self.events)
        self.cancellation = CancellationToken(self.events, self.prompts)
        self._state_lock = threading.Lock()
        self._state = "done"
        created_at = record.get("created_at")
        self._state_changed_at = float(
            created_at if created_at is not None else time.time()
        )
        self._state_busy = False
        self._state_pending = False
        self.events.add_listener(self._track_state)
        self.agent: LLMAgent | None = None
        self.hub_client: HubClient | None = None
        self.server: socket.socket | None = None
        self._shutdown_lock = threading.Lock()
        self._shutdown_started = False

    def _request_stop(self) -> None:
        """Cancel active work and disconnect clients before worker shutdown."""
        with self._shutdown_lock:
            first_request = not self._shutdown_started
            self._shutdown_started = True
            if first_request:
                try:
                    self.record = update_record(
                        self.runtime_id, status="stopping", stopping_at=time.time()
                    )
                except Exception:
                    pass
        self.stop_event.set()
        self.cancellation.cancel()
        prompt = self.prompts.active
        if prompt is not None:
            self.prompts.cancel(prompt.id, source="shutdown")
        agent = self.agent
        if agent is not None:
            try:
                agent.provider.cancel()
            except Exception:
                pass
            try:
                agent._cancel_compaction_provider()
            except Exception:
                pass
        server = self.server
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        with self.connections_lock:
            connections = list(self.connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass
        if first_request:
            threading.Thread(
                target=self._force_exit_after_timeout,
                name="ene-live-shutdown-watchdog",
                daemon=True,
            ).start()

    def _force_exit_after_timeout(self) -> None:
        """Ensure an uninterruptible provider/tool call cannot orphan the worker."""
        if not self.stop_event.wait(15):
            return
        time.sleep(15)
        os._exit(1)

    def _track_state(self, event: AgentEvent) -> None:
        """Record exact working/waiting/done transition times for discovery."""
        with self._state_lock:
            if event.type == "operation_start":
                self._state_busy = True
            elif event.type == "operation_end":
                self._state_busy = False
            elif event.type == "pending_set":
                self._state_pending = True
            elif event.type == "pending_cleared":
                self._state_pending = False
            else:
                return
            state = (
                "working" if self._state_busy
                else "waiting" if self._state_pending
                else "done"
            )
            if state != self._state:
                self._state = state
                self._state_changed_at = event.timestamp

    def _state_timestamp(self, state: str) -> float:
        """Return the tracked transition time, with a fallback for test workers."""
        lock = getattr(self, "_state_lock", None)
        if lock is None:
            return float(self.record.get("created_at", 0))
        with lock:
            if state != self._state:
                # This is only a defensive fallback for state changed outside the
                # brokers' normal event path.
                self._state = state
                self._state_changed_at = time.time()
            return self._state_changed_at

    def _status(self) -> dict[str, Any]:
        agent = self.agent
        operation_id = self.cancellation.operation_id
        submission = self.inputs.submission
        state = (
            "working" if operation_id is not None
            else "waiting" if submission is not None
            else "done"
        )
        status = {
            "runtime_id": self.runtime_id,
            "name": self.record.get("name", ""),
            "conversation_id": getattr(agent, "_session_id", self.record.get("conversation_id", "")),
            "workspace": self.record["workspace"],
            "model": self.record.get("model", ""),
            "attached": self.terminal_attached,
            "busy": operation_id is not None,
            "operation_id": operation_id,
            "needs_attention": state == "done",
            "state_changed_at": self._state_timestamp(state),
            "created_at": self.record.get("created_at", 0),
        }
        if submission is not None:
            status["pending"] = {
                "id": submission.id,
                "text": submission.text,
                "source": submission.source,
                "action_id": submission.action_id,
                "steer": submission.steer,
            }
        prompt = self.prompts.active
        if prompt is not None:
            status["active_prompt"] = {
                "id": prompt.id,
                "kind": prompt.kind,
                "message": prompt.message,
                "choices": prompt.choices,
                "default": prompt.default,
            }
        if agent is not None:
            status["startup"] = agent._startup_details()
            status["commands"] = agent._slash_command_help()
            status["instant_commands"] = sorted(agent.INSTANT_COMMANDS)
            status["instant_listing_commands"] = sorted(
                agent.INSTANT_LISTING_COMMANDS
            )
            status["process_status"] = format_process_status(
                *agent.tool_executor.process_counts(),
                agent.tool_executor.process_activity(),
            )
            get_context_status = getattr(agent, "_status_suffix", None)
            if get_context_status is not None:
                status["context_status"] = get_context_status().event_data()
        return status

    def _make_agent(self) -> LLMAgent:
        options = self.record.get("options", {})
        alias = options.get("model", "")
        models = conf.get("openai", {})
        if not alias:
            if not models:
                raise LiveError(f"No models found in config: {CONFIG_PATH}")
            alias = next(iter(models))
        model_conf = models.get(alias)
        if not isinstance(model_conf, dict):
            raise LiveError(f"Model '{alias}' not found in config: {CONFIG_PATH}")
        provider = model_conf.get("provider", "openai")
        if provider not in provider_names():
            raise LiveError(f"Unknown provider: {provider}")
        console = AgentConsole(events=self.events, render_terminal=False)
        return LLMAgent(
            model=model_conf.get("model", alias),
            api_key=model_conf.get("api_key", ""),
            base_url=model_conf.get("base_url", ""),
            provider_name=provider,
            model_alias=alias,
            verbose=bool(options.get("verbose")),
            stream=bool(options.get("stream", True)),
            reasoning_effort=options.get("reasoning_effort") or model_conf.get("reasoning_effort", "high"),
            context_length=model_conf.get("context_length"),
            max_output_tokens=model_conf.get("max_output_tokens"),
            persona=options.get("persona") or "",
            console=console,
            events=self.events,
            input_broker=self.inputs,
            prompt_broker=self.prompts,
            cancellation=self.cancellation,
            terminal_prompts=False,
            session_name=str(self.record.get("name", "")),
            work_dir=self.record["workspace"],
        )

    def start(self) -> None:
        self.agent = self._make_agent()
        resume = self.record.get("options", {}).get("resume")
        if resume:
            if not self.agent.load_session(resume):
                raise LiveError(f"Could not resume session: {resume}")
        self.agent._initialize_chat_session(resume)
        self.agent._refresh_slash_commands()
        self.agent._session_changed = self._session_changed
        self.agent._session_name_changed = self._session_name_changed
        self.record = update_identity(
            self.runtime_id,
            name=self.agent.session_name,
            workspace=self.record["workspace"],
            conversation_id=self.agent._session_id,
        )
        self.record = update_record(
            self.runtime_id,
            model=self.agent.model_alias,
        )
        self._start_hub()
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen()
        server.settimeout(0.5)
        self.server = server
        self.record = update_record(
            self.runtime_id, status="ready", port=server.getsockname()[1], pid=os.getpid()
        )
        agent_thread = threading.Thread(
            target=self.agent.run_headless_loop, args=(self.stop_event,), name="ene-agent", daemon=True
        )
        agent_thread.start()
        try:
            while not self.stop_event.is_set():
                try:
                    client, _ = server.accept()
                except socket.timeout:
                    continue
                thread = threading.Thread(target=self._handle, args=(client,), daemon=True)
                thread.start()
        finally:
            self._request_stop()
            agent_thread.join(timeout=10)
            if not agent_thread.is_alive():
                try:
                    self.agent.save_session(self.agent._session_id)
                except Exception:
                    pass
            self.agent.close()
            if self.hub_client is not None:
                self.hub_client.stop()
            self._cleanup_record()

    def _session_changed(self, conversation_id: str, name: str) -> None:
        assert self.agent is not None
        self.record = update_identity(
            self.runtime_id,
            name=name,
            workspace=self.record["workspace"],
            conversation_id=conversation_id,
        )
        self._publish_session_name(name)

    def _session_name_changed(self, name: str) -> str:
        assert self.agent is not None
        self.record = update_identity(
            self.runtime_id,
            name=name,
            workspace=self.record["workspace"],
            conversation_id=self.agent._session_id,
        )
        self._publish_session_name(name)
        return name

    def _publish_session_name(self, name: str) -> None:
        assert self.agent is not None
        title = name or Path(self.record["workspace"]).name
        if self.hub_client is not None:
            self.hub_client.meta["name"] = name
            self.hub_client.meta["title"] = title
        self.events.publish(
            "session_meta",
            name=name,
            title=title,
            conversation_id=self.agent._session_id,
        )

    def _cleanup_record(self) -> None:
        """Remove this worker's registry entry without deleting a replacement."""
        path = record_path(self.runtime_id)
        current = read_record(path)
        if current is not None and current.get("token") == self.token:
            unlink_record(path)

    def _start_hub(self) -> None:
        assert self.agent is not None
        info = discover_hub()
        if info is None:
            return
        self.hub_client = HubClient(
            self.events, self.inputs, self.prompts, self.cancellation,
            host=info.get("host", "127.0.0.1"), port=int(info.get("port", 8765)),
            token=info.get("token", ""), session_id=self.runtime_id,
            meta={
                "title": self.record.get("name") or Path(self.record["workspace"]).name,
                "name": self.record.get("name", ""), "cwd": self.record["workspace"],
                "model": self.agent.model_alias, "pid": os.getpid(),
                "host": socket.gethostname(),
            },
        )
        self.hub_client.get_process_status = lambda: format_process_status(
            *self.agent.tool_executor.process_counts(),
            self.agent.tool_executor.process_activity(),
        )
        self.hub_client.start()

    def _handle(self, sock: socket.socket) -> None:
        with self.connections_lock:
            self.connections.add(sock)
        attached = False
        try:
            # A client that connects and then dies without speaking would
            # otherwise park this thread on recv() for the worker's lifetime.
            ready, _, _ = select.select([sock], [], [], REQUEST_TIMEOUT)
            if not ready:
                return
            hello = recv_frame(sock)
            if hello.get("token") != self.token:
                send_frame(sock, {"ok": False, "error": "Forbidden"})
                return
            kind = hello.get("type")
            if kind == "status":
                send_frame(sock, {"ok": True})
                send_frame(sock, {"type": "status", "session": self._status()})
                return
            if kind == "kill":
                send_frame(sock, {"ok": True})
                send_frame(sock, {"type": "stopping"})
                self._request_stop()
                return
            if kind != "attach":
                send_frame(sock, {"ok": False, "error": "Unknown request"})
                return
            with self.terminal_lock:
                if self.terminal_attached:
                    # The code lets a reattach tell this apart from a fatal
                    # rejection and wait for a dead terminal to be released.
                    send_frame(sock, {
                        "ok": False, "code": "attached",
                        "error": "Another terminal is already attached",
                    })
                    return
                self.terminal_attached = True
                attached = True
            send_frame(sock, {"ok": True})
            self._serve_terminal(sock, replay_history=bool(hello.get("replay", True)))
        except (OSError, EOFError, ValueError, LiveError, json.JSONDecodeError):
            pass
        finally:
            if attached:
                with self.terminal_lock:
                    self.terminal_attached = False
            with self.connections_lock:
                self.connections.discard(sock)
            try:
                sock.close()
            except OSError:
                pass

    def _submit_terminal(self, text: str) -> None:
        agent = getattr(self, "agent", None)
        if (
            self.inputs.pending
            and agent is not None
            and text.startswith("/")
            and agent.is_instant_command(text)
        ):
            agent.console.user_input(text, source="terminal")
            agent._run_command(text)
            return
        self.inputs.submit(text, "terminal")

    def _serve_terminal(self, sock: socket.socket, *, replay_history: bool = True) -> None:
        pending: queue.Queue[AgentEvent] = queue.Queue(maxsize=ATTACH_EVENT_QUEUE_SIZE)
        stopped = threading.Event()
        send_lock = threading.Lock()

        def send(message: dict[str, Any]) -> None:
            with send_lock:
                send_frame(sock, message)

        def enqueue(event: AgentEvent) -> None:
            if stopped.is_set():
                return
            try:
                pending.put_nowait(event)
            except queue.Full:
                # A terminal that cannot consume a bounded backlog must
                # reconnect rather than growing the worker without limit.
                stopped.set()
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                # On Windows, shutdown() alone does not unblock a recv()
                # pending in another thread; close() is required to force the
                # attachment to notice the disconnect. On POSIX the earlier
                # shutdown() already released it, so close() is only cleanup.
                try:
                    sock.close()
                except OSError:
                    pass

        # Subscribe before taking the bounded history snapshot. Events racing
        # replay are queued and de-duplicated by sequence, so attach has no gap.
        self.events.add_listener(enqueue)
        events = [event.to_dict() for event in self.events.snapshot()]
        replay = _replay_events(events) if replay_history else []
        replayed_seq = max((int(event.get("seq", 0)) for event in events), default=0)
        session = self._status()
        session["has_replay"] = bool(replay)
        session["show_startup"] = replay_history and not replay
        if session.get("operation_id") is not None:
            indicator = _active_indicator(events)
            if indicator is not None:
                session["active_indicator"] = indicator
        send({"type": "attached", "session": session})

        def forward() -> None:
            nonlocal replayed_seq
            try:
                # Keep replay and live events on one sender so their wire order
                # is stable, while the serving thread remains free to receive
                # submissions and local control actions during a long replay.
                for event in replay:
                    if stopped.is_set():
                        return
                    send({"type": "event", "event": event})
                while not stopped.is_set():
                    try:
                        event = pending.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    if event.seq <= replayed_seq:
                        continue
                    send({"type": "event", "event": event.to_dict()})
                    replayed_seq = event.seq
            except OSError:
                stopped.set()

        sender = threading.Thread(target=forward, name="ene-live-events", daemon=True)
        sender.start()
        try:
            while not self.stop_event.is_set() and not stopped.is_set():
                try:
                    # The attached terminal sends heartbeat pings, so silence
                    # means the shell is gone: a killed one can leave a
                    # half-open connection whose recv() never returns, which
                    # would reserve the session as "attached" forever. Waiting
                    # for readability rather than setting a socket timeout
                    # keeps the deadline off the send path, where a terminal
                    # that briefly stops draining must stay attached until the
                    # bounded event queue decides otherwise.
                    ready, _, _ = select.select([sock], [], [], TERMINAL_IDLE_TIMEOUT)
                    if not ready:
                        return
                    action = recv_frame(sock)
                except (OSError, EOFError, ValueError, LiveError, json.JSONDecodeError):
                    return
                kind = action.get("type")
                if kind == "detach":
                    return
                if kind == "kill":
                    self._request_stop()
                    return
                if kind == "submit":
                    request_id = action.get("request_id")
                    try:
                        self._submit_terminal(str(action.get("text", "")))
                    except ValueError as exc:
                        if request_id is None:
                            self.events.publish("warning", text=str(exc))
                        else:
                            send({
                                "type": "action_result", "request_id": request_id,
                                "ok": False, "error": str(exc),
                            })
                    else:
                        if request_id is not None:
                            send({
                                "type": "action_result", "request_id": request_id,
                                "ok": True,
                            })
                elif kind == "withdraw_pending":
                    request_id = action.get("request_id")
                    item = self.inputs.withdraw(action.get("id"))
                    if request_id is not None:
                        send({
                            "type": "action_result", "request_id": request_id,
                            "ok": item is not None,
                            **({} if item is not None else {
                                "error": "Pending message is no longer available"
                            }),
                        })
                elif kind == "prompt_response":
                    self.prompts.resolve(str(action.get("id", "")), str(action.get("answer", "")), "terminal")
                elif kind == "cancel":
                    self.cancellation.cancel(action.get("operation_id"))
                elif kind == "ping":
                    # Heartbeat from the attached terminal; receiving it just
                    # proves the attachment is still alive.
                    continue
        finally:
            stopped.set()
            self.events.remove_listener(enqueue)
            sender.join(timeout=1)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        return 2
    path = Path(args[0])
    record = read_record(path)
    if record is None:
        return 2
    worker = Worker(record)
    try:
        worker.start()
    except BaseException as exc:
        try:
            update_record(str(record["runtime_id"]), status="error", error=str(exc))
        except Exception:
            pass
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
