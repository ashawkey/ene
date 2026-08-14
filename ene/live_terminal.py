"""Terminal client for a persistent Ene worker."""

from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from functools import partial
from typing import Any, Callable

from prompt_toolkit.patch_stdout import patch_stdout

from ene.live import REQUEST_TIMEOUT, LiveError, connect, recv_frame, send_frame
from ene.terminal import SessionDetach, SessionKill, SessionSwitch, TerminalInput
from ene.ui import AgentConsole, ContextStatus, ResponseStream, ThinkingIndicator
from ene.utils.paths import get_ene_dir


LOCAL_COMMANDS = {
    "detach": "Detach this terminal without stopping the session",
    "switch": "Detach and choose another live session",
    "new": "Detach and start a new live session (/new [name])",
}


class RemotePrompt(Exception):
    pass


class LiveTerminal:
    def __init__(
        self,
        record: dict[str, Any],
        *,
        switch_picker: Callable[[], dict[str, Any] | None] | None = None,
        new_session: Callable[[str], dict[str, Any]] | None = None,
    ):
        self.record = record
        self.switch_picker = switch_picker
        self.new_session = new_session
        self.switch_record: dict[str, Any] | None = None
        self.new_record: dict[str, Any] | None = None
        self.console = AgentConsole()
        self.sock: socket.socket | None = None
        self.send_lock = threading.Lock()
        self.action_lock = threading.Lock()
        self.action_waiters: dict[str, tuple[threading.Event, dict[str, Any]]] = {}
        self.stopped = threading.Event()
        self.detaching = threading.Event()
        self.operation_id: str | None = None
        self.prompt: dict[str, Any] | None = None
        self.pending: dict[str, Any] | None = None
        self.terminal: TerminalInput | None = None
        self.commands: dict[str, str] = {}
        self.instant_commands: set[str] = set()
        self.instant_listing_commands: set[str] = set()
        self.render_lock = threading.Lock()
        self.indicator: ThinkingIndicator | None = None
        self.response_stream: ResponseStream | None = None
        self.assistant_streamed = False
        self.thinking_streamed = False
        self.force_startup_panel = False

    def _show_attach_preamble(self, session: dict[str, Any]) -> None:
        """Show the logo panel for a new session or an explicit session switch."""
        startup = session.get("startup")
        show_startup = session.get("show_startup", not session.get("has_replay"))
        if isinstance(startup, dict) and (self.force_startup_panel or show_startup):
            self.console.startup_panel(**startup)

    def _send(self, message: dict[str, Any]) -> None:
        if self.sock is None:
            raise LiveError("Not attached")
        try:
            with self.send_lock:
                send_frame(self.sock, message)
        except LiveError:
            raise
        except OSError as exc:
            raise LiveError("Connection to the live session was lost") from exc

    def _request(self, message: dict[str, Any]) -> dict[str, Any]:
        """Send a state-changing action and wait for the worker to accept it."""
        request_id = uuid.uuid4().hex
        done = threading.Event()
        response: dict[str, Any] = {}
        with self.action_lock:
            self.action_waiters[request_id] = (done, response)
        try:
            self._send({**message, "request_id": request_id})
            if not done.wait(REQUEST_TIMEOUT):
                raise LiveError("Live-session action timed out")
            return response
        finally:
            with self.action_lock:
                self.action_waiters.pop(request_id, None)

    def run(self) -> tuple[str, str]:
        self.sock = connect(self.record, "attach")
        try:
            attached = recv_frame(self.sock)
            self.sock.settimeout(None)
        except (OSError, EOFError, ValueError, json.JSONDecodeError, LiveError) as exc:
            self.sock.close()
            raise LiveError("Could not attach to the live session") from exc
        session = attached.get("session", {})
        operation_id = session.get("operation_id")
        self.operation_id = operation_id if isinstance(operation_id, str) else None
        pending = session.get("pending")
        self.pending = dict(pending) if isinstance(pending, dict) else None
        label = session.get("name") or str(session.get("runtime_id", ""))[:8]
        workspace = session.get("workspace") or self.record.get("workspace")
        self.commands.update(session.get("commands", {}))
        self.commands.update(LOCAL_COMMANDS)
        self.instant_commands.update(session.get("instant_commands", []))
        self.instant_listing_commands.update(
            session.get("instant_listing_commands", [])
        )
        self.terminal = TerminalInput(
            history_path=str(get_ene_dir(workspace) / "history"),
            work_dir=workspace,
            commands=self.commands,
            system_message=self.console.system,
            persistent=True,
        )
        self.terminal.set_runtime_state(
            cancel=lambda: self._send({"type": "cancel", "operation_id": self.operation_id}),
            pending_text=lambda: self.pending.get("text") if self.pending else None,
            edit_pending=self._edit_pending,
            can_submit_while_pending=self._is_instant_command,
        )
        self.console.interactive_input = True
        self.console.status_sink = self.terminal.set_status
        set_context_status = getattr(self.terminal, "set_context_status", None)
        self.console.context_status_sink = set_context_status
        context_status = session.get("context_status")
        if isinstance(context_status, dict) and set_context_status is not None:
            set_context_status(ContextStatus(
                int(context_status.get("context_tokens", 0)),
                int(context_status.get("context_limit", 0)),
                int(context_status.get("input_tokens", 0)),
                int(context_status.get("output_tokens", 0)),
                int(context_status.get("cached_tokens", 0)),
            ))
        self.terminal.set_process_status(str(session.get("process_status", "")))
        self.terminal.set_busy(self.operation_id is not None)
        active_indicator = session.get("active_indicator")
        if isinstance(active_indicator, dict):
            self._event({"type": "thinking_start", "data": active_indicator})
        active_prompt = session.get("active_prompt")
        if isinstance(active_prompt, dict):
            self.prompt = active_prompt
        self._show_attach_preamble(session)
        self.console.system(
            f"Attached to '{label}'. Use /detach or Ctrl+D to detach; "
            "/switch or Ctrl+S to switch."
        )
        reader = threading.Thread(target=self._read_loop, name="ene-live-reader", daemon=True)
        try:
            with patch_stdout(raw=True):
                reader.start()
                draft = ""
                while not self.stopped.is_set():
                    self.terminal.set_busy(self.operation_id is not None)
                    prompt = self.prompt
                    if prompt is not None:
                        self._answer_prompt(prompt)
                        continue
                    try:
                        text = self.terminal.prompt(
                            default=draft, pre_run=self._interrupt_pending
                        ).strip()
                        draft = ""
                    except RemotePrompt:
                        prompt = self.prompt
                        if prompt is not None:
                            self._answer_prompt(prompt)
                        continue
                    except SessionDetach:
                        self._detach_from_worker()
                        self.console.system("Detached; the session is still running.")
                        return "detach", ""
                    except SessionSwitch:
                        if not self._select_switch():
                            continue
                        self._detach_from_worker()
                        return "switch", ""
                    except SessionKill:
                        self._stop_worker()
                        self.console.system("Session stopped.")
                        return "kill", ""
                    except (EOFError, KeyboardInterrupt):
                        if not self.stopped.is_set():
                            self._detach_from_worker()
                        return "disconnect", ""
                    if not text:
                        continue
                    command, _, argument = text.partition(" ")
                    command = command.lower()
                    if command == "/detach":
                        self._detach_from_worker()
                        self.console.system("Detached; the session is still running.")
                        return "detach", ""
                    if command == "/switch":
                        if not self._select_switch():
                            continue
                        self._detach_from_worker()
                        return "switch", ""
                    if command in {"/exit", "/quit"}:
                        self._stop_worker()
                        self.console.system("Session stopped.")
                        return "kill", ""
                    if command == "/new":
                        if not self._start_new(argument.strip()):
                            continue
                        self._detach_from_worker()
                        return "new", argument.strip()
                    response = self._request({"type": "submit", "text": text})
                    if not response.get("ok"):
                        self.console.warn(str(response.get("error", "Message was rejected")))
                        draft = text
            return "disconnect", ""
        finally:
            self.stopped.set()
            with self.render_lock:
                if self.indicator is not None:
                    self.indicator.__exit__(None, None, None)
                    self.indicator = None
                if self.response_stream is not None:
                    self.response_stream.close()
                    self.response_stream = None
            self.console.interactive_input = False
            self.console.status_sink = None
            self.console.context_status_sink = None
            try:
                self.sock.close()
            except OSError:
                pass

    def _select_switch(self) -> bool:
        """Choose a replacement while keeping this attachment alive on cancel."""
        if self.switch_picker is None:
            return True
        self.switch_record = self.switch_picker()
        return self.switch_record is not None

    def _start_new(self, name: str) -> bool:
        """Start a replacement before detaching, so failure keeps this session."""
        if self.new_session is None:
            return True
        try:
            self.new_record = self.new_session(name)
        except LiveError as exc:
            self.console.warn(str(exc))
            return False
        return True

    def _stop_worker(self) -> None:
        """Wait for shutdown so replay rendering cannot outlive the terminal."""
        self.detaching.set()
        self._send({"type": "kill"})
        self.stopped.wait()

    def _detach_from_worker(self) -> None:
        """Wait until the worker releases this attachment before reconnecting."""
        self.detaching.set()
        self._send({"type": "detach"})
        # The worker clears terminal_attached before closing the socket. Waiting
        # for the reader to observe that close prevents Ctrl+S from racing an
        # immediate reattach to the same session.
        self.stopped.wait()

    def _is_instant_command(self, text: str) -> bool:
        parts = text.split(maxsplit=1)
        if not parts or not parts[0].startswith("/"):
            return False
        command = parts[0][1:].lower()
        if command in LOCAL_COMMANDS:
            return True
        if command not in self.commands:
            return True
        return command in self.instant_commands or (
            command in self.instant_listing_commands and len(parts) == 1
        )

    def _edit_pending(self) -> str | None:
        pending = self.pending
        if pending is None:
            return None
        response = self._request({
            "type": "withdraw_pending", "id": pending.get("id")
        })
        if not response.get("ok"):
            self.console.warn(str(response.get(
                "error", "Pending message is no longer available"
            )))
            return None
        self.pending = None
        return str(pending.get("text", ""))

    def _read_loop(self) -> None:
        assert self.sock is not None
        try:
            while not self.stopped.is_set():
                message = recv_frame(self.sock)
                if message.get("type") == "event":
                    self._event(message.get("event", {}))
                elif message.get("type") == "action_result":
                    request_id = str(message.get("request_id", ""))
                    with self.action_lock:
                        waiter = self.action_waiters.get(request_id)
                    if waiter is not None:
                        done, response = waiter
                        response.update(message)
                        done.set()
        except (OSError, EOFError, ValueError, json.JSONDecodeError, LiveError):
            if not self.stopped.is_set() and not self.detaching.is_set():
                self.console.warn("Connection to the session was lost.")
            self.stopped.set()
            with self.action_lock:
                waiters = list(self.action_waiters.values())
            for done, response in waiters:
                response.update(ok=False, error="Connection to the session was lost")
                done.set()
            self._interrupt_editor(EOFError)

    def _interrupt_pending(self) -> None:
        """Close an editor that raced a remote prompt or disconnect."""
        if self.stopped.is_set():
            self._interrupt_editor(EOFError)
        elif self.prompt is not None:
            self._interrupt_editor(RemotePrompt)

    def _interrupt_editor(self, exception: type[BaseException]) -> None:
        terminal = self.terminal
        if terminal is None or not terminal.app.is_running:
            return
        app = terminal.app
        callback = partial(app.exit, exception=exception)
        loop = getattr(app, "loop", None)
        if loop is not None:
            loop.call_soon_threadsafe(callback)
        else:
            callback()

    def _event(self, event: dict[str, Any]) -> None:
        kind = event.get("type", "")
        data = event.get("data", {}) or {}
        if kind == "operation_start":
            self.operation_id = data.get("id")
        elif kind == "operation_end":
            if self.operation_id == data.get("id"):
                self.operation_id = None
        elif kind == "pending_set":
            self.pending = data
        elif kind == "pending_cleared":
            if self.pending and self.pending.get("id") == data.get("id"):
                self.pending = None
        elif kind == "prompt_open":
            self.prompt = data
            self._interrupt_editor(RemotePrompt)
        elif kind == "prompt_resolved":
            self.prompt = None
        elif kind == "thinking_start":
            context = None
            if "context_tokens" in data:
                context = ContextStatus(
                    int(data.get("context_tokens", 0)),
                    int(data.get("context_limit", 0)),
                    int(data.get("input_tokens", 0)),
                    int(data.get("output_tokens", 0)),
                    int(data.get("cached_tokens", 0)),
                )
            if self.indicator is not None:
                self.indicator.__exit__(None, None, None)
            self.indicator = self.console.thinking(
                label=str(data.get("label", "Working")),
                status_suffix=context or str(data.get("suffix", "")),
                progress=bool(data.get("progress", False)),
                countdown=data.get("countdown"),
                initial_elapsed=max(
                    0.0, time.time() - float(data.get("started_at", time.time()))
                ),
                round_elapsed=data.get("round_elapsed"),
            )
            self.indicator.__enter__()
        elif kind == "thinking_stop" and self.indicator is not None:
            self.indicator.__exit__(None, None, None)
            self.indicator = None
        elif kind == "process_status" and self.terminal is not None:
            self.terminal.set_process_status(str(data.get("text", "")))
        elif kind == "commands":
            self.commands.clear()
            self.commands.update(data.get("commands", {}))
            self.commands.update(LOCAL_COMMANDS)
        elif kind == "draft_set" and self.terminal is not None:
            buffer = self.terminal.app.current_buffer
            buffer.text = str(data.get("text", ""))
            buffer.cursor_position = len(buffer.text)
        if self.terminal is not None:
            self.terminal.set_busy(self.operation_id is not None)
        self._render(kind, data)

    def _answer_prompt(self, data: dict[str, Any]) -> None:
        kind = data.get("kind")
        if kind == "select":
            answer = self.console.select_terminal(
                str(data.get("message", "")), list(data.get("choices", [])),
                default=data.get("default") or None,
            )
        else:
            answer = self.console.ask_text_terminal(
                str(data.get("message", "")), default=str(data.get("default", ""))
            )
        if answer is None:
            return
        self._send({"type": "prompt_response", "id": data.get("id"), "answer": answer})
        if self.prompt is data or (
            self.prompt is not None and self.prompt.get("id") == data.get("id")
        ):
            self.prompt = None

    def _render(self, kind: str, data: dict[str, Any]) -> None:
        with self.render_lock:
            self._render_unlocked(kind, data)

    def _render_unlocked(self, kind: str, data: dict[str, Any]) -> None:
        text = str(data.get("text", ""))
        if kind == "rule":
            self.console.rule()
        elif kind == "user_message":
            self.console.user_input(text, with_rule=False)
        elif kind == "assistant_delta":
            if self.response_stream is None:
                self.response_stream = self.console.stream_response(show_thinking=True)
            self.response_stream.on_content(text)
            self.assistant_streamed = True
        elif kind == "assistant_message":
            if self.response_stream is not None:
                self.response_stream.close()
                self.response_stream = None
            else:
                self.console.response(text)
            self.assistant_streamed = False
        elif kind == "system":
            self.console.system(text)
        elif kind == "warning":
            self.console.warn(text)
        elif kind == "error":
            self.console.error(text)
        elif kind == "debug":
            self.console.debug(text)
        elif kind == "output":
            ansi = data.get("ansi")
            if isinstance(ansi, str):
                self.console.stream_output(ansi, dim=False)
            else:
                self.console.print(text, markup=False)
        elif kind == "tool_start":
            self.console.tool(
                text, name=data.get("name"), primary=str(data.get("primary", "")),
                qualifiers=list(data.get("qualifiers", []) or []),
            )
        elif kind == "tool_result":
            self.console.tool_result(text, bool(data.get("success", True)))
        elif kind == "diff":
            self.console.diff_edit(
                str(data.get("path", "")), str(data.get("old_text", "")),
                str(data.get("new_text", "")), data.get("line_num"),
                int(data.get("count", 1)), bool(data.get("success", True)),
            )
        elif kind == "thinking_delta":
            if self.response_stream is None:
                self.response_stream = self.console.stream_response(show_thinking=True)
            self.response_stream.on_thinking(text)
            self.thinking_streamed = True
        elif kind == "thinking":
            if not self.thinking_streamed:
                self.console.thinking_message(text)
            self.thinking_streamed = False
        elif kind == "timeline_reset":
            self.console.reset_timeline()
