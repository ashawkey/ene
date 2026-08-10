import socket
import threading
import time
from types import SimpleNamespace

import pytest

from ene import live
from ene import live_worker
from ene.live_terminal import LiveTerminal
from ene.live_worker import Worker, _active_indicator, _replay_events
from ene.ui import ContextStatus
from ene.utils.io import CancellationToken, EventHub, InputBroker, PromptBroker


def test_validate_name_rejects_empty_and_control_characters():
    assert live.validate_name("  useful work  ") == "useful work"
    with pytest.raises(live.LiveError):
        live.validate_name("  ")
    with pytest.raises(live.LiveError):
        live.validate_name("bad\nname")


def test_framed_protocol_round_trip():
    left, right = socket.socketpair()
    try:
        live.send_frame(left, {"type": "event", "text": "héllo"})
        assert live.recv_frame(right) == {"type": "event", "text": "héllo"}
    finally:
        left.close()
        right.close()


def test_connect_keeps_bounded_timeout_after_acknowledgement(monkeypatch):
    class FakeSocket:
        def __init__(self):
            self.timeouts = []
            self.closed = False

        def settimeout(self, timeout):
            self.timeouts.append(timeout)

        def close(self):
            self.closed = True

    sock = FakeSocket()
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: sock)
    monkeypatch.setattr(live, "send_frame", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(live, "recv_frame", lambda _sock: {"ok": True})

    connected = live.connect({"port": 1234, "token": "secret"}, "status")

    assert connected is sock
    assert sock.timeouts == [live.REQUEST_TIMEOUT]
    assert not sock.closed


def test_connect_normalizes_transport_failure(monkeypatch):
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionRefusedError()),
    )

    with pytest.raises(live.LiveError, match="Could not connect"):
        live.connect({"port": 1234}, "status")


def test_kill_session_waits_for_worker_process_exit(monkeypatch, tmp_path):
    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

    record = {"runtime_id": "runtime", "pid": 123, "token": "secret"}
    monkeypatch.setattr(live, "LIVE_DIR", tmp_path / "live")
    monkeypatch.setattr(live, "REGISTRY_LOCK", tmp_path / "live" / ".lock")
    monkeypatch.setattr(live, "connect", lambda *_args, **_kwargs: FakeSocket())
    monkeypatch.setattr(live, "recv_frame", lambda _sock: {"type": "stopping"})
    waited = []
    monkeypatch.setattr(
        live,
        "_wait_for_process_exit",
        lambda pid, timeout: waited.append((pid, timeout)) or True,
    )

    live.kill_session(record)

    assert waited == [(123, live.STOP_TIMEOUT)]


def test_unlink_record_removes_worker_log(tmp_path):
    path = tmp_path / "runtime.json"
    log_path = path.with_suffix(".log")
    path.write_text("{}")
    log_path.write_text("startup output")

    live.unlink_record(path)

    assert not path.exists()
    assert not log_path.exists()


def test_list_records_keeps_stopping_worker_reserved(monkeypatch, tmp_path):
    monkeypatch.setattr(live, "LIVE_DIR", tmp_path / "live")
    monkeypatch.setattr(live, "REGISTRY_LOCK", tmp_path / "live" / ".lock")
    record = live.create_record(name="reserved", workspace=str(tmp_path), options={})
    live.update_record(
        record["runtime_id"], status="stopping", stopping_at=time.time()
    )
    monkeypatch.setattr(live, "probe", lambda _record: None)

    records = live.list_records()

    assert [item["name"] for item in records] == ["reserved"]
    assert live.record_path(record["runtime_id"]).exists()


def test_live_worker_marks_record_stopping_before_closing_listener(monkeypatch):
    worker = Worker.__new__(Worker)
    worker.runtime_id = "runtime"
    worker.record = {"runtime_id": "runtime"}
    worker._shutdown_lock = threading.Lock()
    worker._shutdown_started = False
    worker.stop_event = threading.Event()
    worker.cancellation = SimpleNamespace(cancel=lambda: None)
    worker.prompts = SimpleNamespace(active=None)
    worker.agent = None
    worker.server = SimpleNamespace(close=lambda: closed.append(True))
    worker.connections_lock = threading.Lock()
    worker.connections = set()
    updates = []
    closed = []
    monkeypatch.setattr(
        live_worker,
        "update_record",
        lambda runtime_id, **changes: updates.append((runtime_id, changes))
        or worker.record | changes,
    )
    monkeypatch.setattr(worker, "_force_exit_after_timeout", lambda: None)

    worker._request_stop()

    assert updates[0][0] == "runtime"
    assert updates[0][1]["status"] == "stopping"
    assert closed == [True]


def test_event_hub_listeners_receive_events_and_can_be_removed():
    events = EventHub()
    received = []
    received_append = received.append
    events.add_listener(received_append)
    first = events.publish("system", text="one")
    events.remove_listener(received_append)
    events.publish("system", text="two")
    assert received == [first]


def test_event_hub_listener_delivery_follows_sequence_across_publishers():
    events = EventHub()
    received = []
    first_entered = threading.Event()
    release_first = threading.Event()

    def listener(event):
        if event.seq == 1:
            first_entered.set()
            assert release_first.wait(timeout=2)
        received.append(event.seq)

    events.add_listener(listener)
    first = threading.Thread(target=events.publish, args=("system",), kwargs={"text": "one"})
    second = threading.Thread(target=events.publish, args=("system",), kwargs={"text": "two"})
    first.start()
    assert first_entered.wait(timeout=2)
    second.start()
    time.sleep(0.05)
    assert received == []

    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert received == [1, 2]


def test_event_hub_listener_failure_does_not_break_publication():
    events = EventHub()
    received = []

    def fail(_event):
        raise OSError("disk full")

    events.add_listener(fail)
    events.add_listener(received.append)

    event = events.publish("system", text="still delivered")

    assert received == [event]
    assert events.snapshot() == [event]


def test_resolve_rejects_worker_that_is_still_starting(monkeypatch, tmp_path):
    monkeypatch.setattr(live, "LIVE_DIR", tmp_path / "live")
    monkeypatch.setattr(live, "REGISTRY_LOCK", tmp_path / "live" / ".lock")
    record = live.create_record(name="warming", workspace=str(tmp_path), options={})

    with pytest.raises(live.LiveError, match="still starting"):
        live.resolve(record["runtime_id"])


def test_create_record_enforces_unique_live_name(monkeypatch, tmp_path):
    monkeypatch.setattr(live, "LIVE_DIR", tmp_path / "live")
    monkeypatch.setattr(live, "REGISTRY_LOCK", tmp_path / "live" / ".lock")
    first = live.create_record(name="same", workspace=str(tmp_path), options={})
    with pytest.raises(live.LiveError, match="already exists"):
        live.create_record(name="same", workspace=str(tmp_path), options={})
    live.record_path(first["runtime_id"]).unlink()


class _Buffer:
    def __init__(self):
        self.text = ""
        self.cursor_position = 0


class _App:
    def __init__(self):
        self.current_buffer = _Buffer()
        self.is_running = False


class _Terminal:
    def __init__(self):
        self.busy = None
        self.process_status = None
        self.app = _App()

    def set_busy(self, busy):
        self.busy = busy

    def set_process_status(self, status):
        self.process_status = status


class _Indicator:
    def __init__(self):
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *_):
        self.exited = True


class _Console:
    def __init__(self):
        self.thinking_args = None
        self.indicator = _Indicator()

    def thinking(self, **kwargs):
        self.thinking_args = kwargs
        return self.indicator


def test_live_worker_status_includes_prompt_opened_before_attach():
    events = EventHub()
    worker = Worker.__new__(Worker)
    worker.runtime_id = "runtime"
    worker.record = {"workspace": "/tmp", "created_at": 0}
    worker.terminal_attached = False
    worker.events = events
    worker.inputs = SimpleNamespace(submission=None)
    worker.prompts = PromptBroker(events)
    worker.cancellation = SimpleNamespace(operation_id=None)
    worker.agent = None
    result = []
    prompt_thread = threading.Thread(
        target=lambda: result.append(worker.prompts.ask("text", "Detached question"))
    )
    prompt_thread.start()
    deadline = time.monotonic() + 1
    while worker.prompts.active is None and time.monotonic() < deadline:
        time.sleep(0.01)

    status = worker._status()

    assert status["active_prompt"]["message"] == "Detached question"
    worker.prompts.cancel(status["active_prompt"]["id"])
    prompt_thread.join(timeout=1)


def test_live_worker_status_includes_pending_submission_and_operation_id():
    worker = Worker.__new__(Worker)
    worker.runtime_id = "runtime"
    worker.record = {"workspace": "/tmp", "created_at": 0}
    worker.terminal_attached = False
    worker.inputs = SimpleNamespace(submission=SimpleNamespace(
        id="pending-1",
        text="follow up",
        source="terminal",
        action_id=None,
        steer=False,
    ))
    worker.prompts = SimpleNamespace(active=None)
    worker.cancellation = SimpleNamespace(operation_id="operation-1")
    worker.agent = None

    status = worker._status()

    assert status["operation_id"] == "operation-1"
    assert status["busy"] is True
    assert status["pending"] == {
        "id": "pending-1",
        "text": "follow up",
        "source": "terminal",
        "action_id": None,
        "steer": False,
    }


def test_live_worker_runs_instant_terminal_command_despite_pending_input():
    events = EventHub()
    inputs = InputBroker(events)
    queued = inputs.submit("follow up")
    dispatched = []
    worker = Worker.__new__(Worker)
    worker.inputs = inputs
    worker.agent = SimpleNamespace(
        is_instant_command=lambda text: text == "/context",
        console=SimpleNamespace(user_input=lambda *args, **kwargs: None),
        _run_command=dispatched.append,
    )

    worker._submit_terminal("/context")

    assert dispatched == ["/context"]
    assert inputs.submission == queued


def test_live_terminal_classifies_instant_commands_from_worker_metadata():
    client = LiveTerminal({})
    client.commands = {
        "context": "Show context",
        "compact": "Compact context",
        "name": "Show or set name",
    }
    client.instant_commands = {"context", "name"}
    client.instant_listing_commands = set()

    assert client._is_instant_command("/context")
    assert client._is_instant_command("/context 3")
    assert client._is_instant_command("/name")
    assert client._is_instant_command("/name new name")
    assert not client._is_instant_command("/compact")
    assert not client._is_instant_command("follow up")


def test_live_terminal_keeps_cancelled_remote_prompt_open():
    client = LiveTerminal({})
    prompt = {
        "id": "prompt-1",
        "kind": "text",
        "message": "Continue?",
        "default": "",
    }
    client.prompt = prompt
    client.console = SimpleNamespace(ask_text_terminal=lambda *_args, **_kwargs: None)
    sent = []
    client._send = sent.append

    client._answer_prompt(prompt)

    assert client.prompt is prompt
    assert sent == []


def test_live_terminal_answers_prompt_replayed_before_editor_starts():
    events = EventHub()
    prompts = PromptBroker(events)
    result = []
    worker = threading.Thread(
        target=lambda: result.append(prompts.ask("text", "Detached question"))
    )
    worker.start()
    deadline = time.monotonic() + 1
    while prompts.active is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert prompts.active is not None

    client = LiveTerminal({})
    client.terminal = _Terminal()
    client.console = SimpleNamespace(ask_text_terminal=lambda *_args, **_kwargs: "answer")
    client._send = lambda message: prompts.resolve(
        str(message["id"]), str(message["answer"]), "terminal"
    )
    client._event({
        "type": "prompt_open",
        "data": {
            "id": prompts.active.id,
            "kind": "text",
            "message": "Detached question",
            "default": "",
        },
    })

    client._interrupt_pending()
    assert client.prompt is not None
    client._answer_prompt(client.prompt)
    worker.join(timeout=1)

    assert result == ["answer"]
    assert client.prompt is None


def test_live_terminal_hydrates_pending_and_operation_from_attach(monkeypatch):
    class FakeSocket:
        def settimeout(self, _timeout):
            pass

        def close(self):
            pass

    class FakeTerminal:
        def __init__(self, **_kwargs):
            self.app = SimpleNamespace(is_running=False)
            self.busy = []

        def set_runtime_state(self, **state):
            self.runtime_state = state

        def set_process_status(self, _status):
            pass

        def set_status(self, _status):
            pass

        def set_busy(self, busy):
            self.busy.append(busy)

        def prompt(self, **_kwargs):
            assert client.stopped.wait(timeout=1)
            raise EOFError

    attached = {
        "type": "attached",
        "session": {
            "runtime_id": "runtime",
            "workspace": "/tmp",
            "operation_id": "operation-1",
            "pending": {"id": "pending-1", "text": "follow up"},
        },
    }
    calls = 0

    def receive(_sock):
        nonlocal calls
        calls += 1
        if calls == 1:
            return attached
        raise EOFError

    terminal = FakeTerminal()
    monkeypatch.setattr("ene.live_terminal.connect", lambda *_args: FakeSocket())
    monkeypatch.setattr("ene.live_terminal.recv_frame", receive)
    monkeypatch.setattr(
        "ene.live_terminal.TerminalInput", lambda **_kwargs: terminal
    )
    client = LiveTerminal({})
    client.console = SimpleNamespace(
        interactive_input=False,
        status_sink=None,
        system=lambda _text: None,
        warn=lambda _text: None,
    )

    result = client.run()

    assert result == ("disconnect", "")
    assert client.operation_id == "operation-1"
    assert client.pending == {"id": "pending-1", "text": "follow up"}
    assert terminal.busy == [True]
    sent = []
    client._send = sent.append
    client._request = lambda message: sent.append(message) or {"ok": True}
    terminal.runtime_state["cancel"]()
    assert client._edit_pending() == "follow up"
    assert sent == [
        {"type": "cancel", "operation_id": "operation-1"},
        {"type": "withdraw_pending", "id": "pending-1"},
    ]


def test_live_terminal_normalizes_disconnect_during_attach(monkeypatch):
    class FakeSocket:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    sock = FakeSocket()
    monkeypatch.setattr("ene.live_terminal.connect", lambda *_args, **_kwargs: sock)
    monkeypatch.setattr(
        "ene.live_terminal.recv_frame",
        lambda _sock: (_ for _ in ()).throw(EOFError()),
    )
    client = LiveTerminal({})

    with pytest.raises(live.LiveError, match="Could not attach"):
        client.run()

    assert sock.closed


def test_live_terminal_keeps_pending_message_when_withdrawal_is_rejected():
    client = LiveTerminal({})
    pending = {"id": "pending-1", "text": "already running"}
    client.pending = pending
    warnings = []
    client.console = SimpleNamespace(warn=warnings.append)
    client._request = lambda _message: {
        "ok": False, "error": "Pending message is no longer available"
    }

    assert client._edit_pending() is None
    assert client.pending is pending
    assert warnings == ["Pending message is no longer available"]


def test_live_terminal_cancelled_switch_keeps_attachment():
    client = LiveTerminal({}, switch_picker=lambda: None)
    detached = []
    client._detach_from_worker = lambda: detached.append(True)

    assert client._select_switch() is False
    assert client.switch_record is None
    assert detached == []


def test_live_terminal_switch_forces_startup_panel_despite_replay():
    client = LiveTerminal({})
    panels = []
    client.console = SimpleNamespace(startup_panel=lambda **details: panels.append(details))
    client.force_startup_panel = True
    startup = {
        "model": "test",
        "context": "100K",
        "reasoning": "high",
        "persona": "coder",
        "skills": "1 available",
        "workspace": "/tmp/project",
    }

    client._show_attach_preamble({
        "startup": startup,
        "has_replay": True,
        "show_startup": False,
    })

    assert panels == [startup]


def test_live_terminal_regular_reattach_does_not_repeat_startup_panel():
    client = LiveTerminal({})
    panels = []
    client.console = SimpleNamespace(startup_panel=lambda **details: panels.append(details))

    client._show_attach_preamble({
        "startup": {"model": "test"},
        "has_replay": True,
        "show_startup": False,
    })

    assert panels == []


def test_live_terminal_new_failure_keeps_current_attachment():
    client = LiveTerminal(
        {}, new_session=lambda _name: (_ for _ in ()).throw(
            live.LiveError("A live session named 'taken' already exists")
        )
    )
    warnings = []
    detached = []
    client.console = SimpleNamespace(warn=warnings.append)
    client._detach_from_worker = lambda: detached.append(True)

    assert client._start_new("taken") is False
    assert client.new_record is None
    assert warnings == ["A live session named 'taken' already exists"]
    assert detached == []


def test_live_terminal_stop_waits_for_worker_disconnect():
    client = LiveTerminal({})
    sent = []
    finished = threading.Event()

    def disconnect():
        assert client.detaching.wait(timeout=1)
        time.sleep(0.02)
        client.stopped.set()
        finished.set()

    worker = threading.Thread(target=disconnect)
    worker.start()
    client._send = sent.append

    client._stop_worker()
    worker.join(timeout=1)

    assert sent == [{"type": "kill"}]
    assert finished.is_set()


def test_live_terminal_detach_waits_for_worker_disconnect():
    client = LiveTerminal({})
    sent = []
    finished = threading.Event()

    def disconnect():
        assert client.detaching.wait(timeout=1)
        time.sleep(0.02)
        client.stopped.set()
        finished.set()

    worker = threading.Thread(target=disconnect)
    worker.start()
    client._send = sent.append

    client._detach_from_worker()
    worker.join(timeout=1)

    assert sent == [{"type": "detach"}]
    assert finished.is_set()


def test_live_terminal_interrupts_running_editor_when_connection_stops():
    client = LiveTerminal({})
    terminal = _Terminal()
    terminal.app.is_running = True
    terminal.app.loop = SimpleNamespace(call_soon_threadsafe=lambda callback: callback())
    terminal.app.exit = lambda **kwargs: setattr(terminal.app, "exception", kwargs["exception"])
    client.terminal = terminal
    client.stopped.set()

    client._interrupt_pending()

    assert terminal.app.exception is EOFError


def test_live_terminal_reconstructs_remote_status_bar():
    client = LiveTerminal({})
    client.terminal = _Terminal()
    client.console = _Console()

    client._event({
        "type": "thinking_start",
        "data": {
            "label": "Executing",
            "context_tokens": 25,
            "context_limit": 100,
            "input_tokens": 20,
            "output_tokens": 5,
            "started_at": 0,
            "round_elapsed": 12,
        },
    })

    status = client.console.thinking_args["status_suffix"]
    assert isinstance(status, ContextStatus)
    assert status.fraction == 0.25
    assert client.console.thinking_args["label"] == "Executing"
    assert client.console.thinking_args["round_elapsed"] == 12
    assert client.console.indicator.entered

    client._event({"type": "thinking_stop", "data": {}})
    assert client.console.indicator.exited
    assert client.indicator is None

    client._event({"type": "process_status", "data": {"text": "1 process"}})
    assert client.terminal.process_status == "1 process"

    client._event({"type": "draft_set", "data": {"text": "restored prompt"}})
    assert client.terminal.app.current_buffer.text == "restored prompt"
    assert client.terminal.app.current_buffer.cursor_position == len("restored prompt")


def test_live_worker_accepts_input_while_replay_is_still_sending(monkeypatch):
    left, right = socket.socketpair()
    worker = Worker.__new__(Worker)
    worker.events = EventHub()
    worker.events.publish("user_message", text="current")
    worker.events.publish("iteration_start", iteration=1)
    worker.stop_event = threading.Event()
    worker.inputs = InputBroker(worker.events)
    worker.prompts = PromptBroker(worker.events)
    worker.cancellation = CancellationToken(worker.events, worker.prompts)
    worker._status = lambda: {"runtime_id": "runtime"}
    replay_started = threading.Event()
    release_replay = threading.Event()
    real_send_frame = live.send_frame

    def delayed_send(sock, message):
        if message.get("type") == "event" and not replay_started.is_set():
            replay_started.set()
            assert release_replay.wait(timeout=1)
        real_send_frame(sock, message)

    monkeypatch.setattr("ene.live_worker.send_frame", delayed_send)
    serving = threading.Thread(target=worker._serve_terminal, args=(right,))
    serving.start()

    attached = live.recv_frame(left)
    assert attached["session"]["has_replay"] is True
    assert replay_started.wait(timeout=1)
    live.send_frame(left, {"type": "submit", "text": "typed during replay"})
    deadline = time.monotonic() + 1
    while worker.inputs.submission is None and time.monotonic() < deadline:
        time.sleep(0.01)

    assert worker.inputs.submission is not None
    assert worker.inputs.submission.text == "typed during replay"
    release_replay.set()
    live.recv_frame(left)
    live.send_frame(left, {"type": "detach"})
    serving.join(timeout=1)
    left.close()
    right.close()
    assert not serving.is_alive()


def test_live_worker_acknowledges_accepted_and_rejected_submissions():
    left, right = socket.socketpair()
    worker = Worker.__new__(Worker)
    worker.events = EventHub()
    worker.stop_event = threading.Event()
    worker.inputs = InputBroker(worker.events)
    worker.prompts = PromptBroker(worker.events)
    worker.cancellation = CancellationToken(worker.events, worker.prompts)
    worker._status = lambda: {"runtime_id": "runtime"}
    serving = threading.Thread(target=worker._serve_terminal, args=(right,))
    serving.start()

    live.recv_frame(left)  # attached
    live.send_frame(left, {
        "type": "submit", "request_id": "first", "text": "accepted"
    })

    def receive_result(request_id):
        while True:
            message = live.recv_frame(left)
            if (
                message.get("type") == "action_result"
                and message.get("request_id") == request_id
            ):
                return message

    assert receive_result("first")["ok"] is True
    live.send_frame(left, {
        "type": "submit", "request_id": "second", "text": "rejected"
    })
    rejected = receive_result("second")
    assert rejected["ok"] is False
    assert rejected["error"] == "Another message is already pending."
    assert worker.inputs.submission.text == "accepted"

    live.send_frame(left, {"type": "detach"})
    serving.join(timeout=1)
    left.close()
    right.close()
    assert not serving.is_alive()


def test_live_worker_acknowledges_stale_pending_withdrawal():
    left, right = socket.socketpair()
    worker = Worker.__new__(Worker)
    worker.events = EventHub()
    worker.stop_event = threading.Event()
    worker.inputs = InputBroker(worker.events)
    worker.prompts = PromptBroker(worker.events)
    worker.cancellation = CancellationToken(worker.events, worker.prompts)
    worker._status = lambda: {"runtime_id": "runtime"}
    serving = threading.Thread(target=worker._serve_terminal, args=(right,))
    serving.start()

    live.recv_frame(left)  # attached
    live.send_frame(left, {
        "type": "withdraw_pending", "request_id": "withdraw", "id": "stale"
    })
    result = live.recv_frame(left)
    assert result == {
        "type": "action_result",
        "request_id": "withdraw",
        "ok": False,
        "error": "Pending message is no longer available",
    }

    live.send_frame(left, {"type": "detach"})
    serving.join(timeout=1)
    left.close()
    right.close()
    assert not serving.is_alive()


def test_live_worker_disconnects_attachment_when_event_backlog_fills(monkeypatch):
    left, right = socket.socketpair()
    worker = Worker.__new__(Worker)
    worker.events = EventHub()
    worker.events.publish("user_message", text="current")
    worker.events.publish("iteration_start", iteration=1)
    worker.stop_event = threading.Event()
    worker._status = lambda: {"runtime_id": "runtime"}
    replay_started = threading.Event()
    release_replay = threading.Event()
    real_send_frame = live.send_frame

    def delayed_send(sock, message):
        if message.get("type") == "event" and not replay_started.is_set():
            replay_started.set()
            assert release_replay.wait(timeout=1)
        real_send_frame(sock, message)

    monkeypatch.setattr(live_worker, "ATTACH_EVENT_QUEUE_SIZE", 2)
    monkeypatch.setattr("ene.live_worker.send_frame", delayed_send)
    serving = threading.Thread(target=worker._serve_terminal, args=(right,))
    serving.start()

    attached = live.recv_frame(left)
    assert attached["session"]["has_replay"] is True
    assert replay_started.wait(timeout=1)
    worker.events.publish("system", text="one")
    worker.events.publish("system", text="two")
    worker.events.publish("system", text="overflow")
    release_replay.set()
    serving.join(timeout=1)
    left.close()
    right.close()

    assert not serving.is_alive()


def test_live_worker_can_attach_without_replaying_history():
    left, right = socket.socketpair()
    worker = Worker.__new__(Worker)
    worker.events = EventHub()
    worker.events.publish("user_message", text="current")
    worker.stop_event = threading.Event()
    worker._status = lambda: {"runtime_id": "runtime"}
    serving = threading.Thread(
        target=worker._serve_terminal,
        args=(right,),
        kwargs={"replay_history": False},
    )
    serving.start()

    attached = live.recv_frame(left)
    live.send_frame(left, {"type": "detach"})
    serving.join(timeout=1)
    left.close()
    right.close()

    assert attached["session"]["has_replay"] is False
    assert attached["session"]["show_startup"] is False
    assert not serving.is_alive()


def test_live_worker_attach_restores_only_current_active_indicator():
    left, right = socket.socketpair()
    worker = Worker.__new__(Worker)
    worker.events = EventHub()
    worker.events.publish("thinking_start", label="Old", started_at=1.0)
    worker.events.publish("thinking_stop")
    worker.events.publish("thinking_start", label="Executing", started_at=123.0)
    worker.stop_event = threading.Event()
    worker._status = lambda: {"runtime_id": "runtime", "operation_id": "operation"}
    serving = threading.Thread(target=worker._serve_terminal, args=(right,))
    serving.start()

    attached = live.recv_frame(left)
    live.send_frame(left, {"type": "detach"})
    serving.join(timeout=1)
    left.close()
    right.close()

    assert attached["session"]["active_indicator"] == {
        "label": "Executing", "started_at": 123.0
    }
    assert not serving.is_alive()


def test_live_worker_replays_full_history_compactly_in_original_order():
    events = []
    seq = 0
    for turn in range(1, 13):
        for kind, data in [
            ("user_message", {"text": f"prompt {turn}"}),
            ("iteration_start", {"iteration": 1}),
            ("assistant_message", {"text": f"intermediate {turn}"}),
            ("tool_start", {"text": "read_file"}),
            ("tool_result", {"text": "many lines"}),
            ("warning", {"text": "historical warning"}),
            ("iteration_start", {"iteration": 2}),
            ("assistant_message", {"text": f"final {turn}"}),
        ]:
            seq += 1
            events.append({"seq": seq, "type": kind, "data": data})

    replay = _replay_events(events)

    assert replay[0]["data"]["text"] == "prompt 1"
    assert replay[1]["data"]["text"] == "4 messages hidden"
    assert replay[2]["data"]["text"] == "final 1"
    assert replay[-1]["data"]["text"] == "final 12"
    assert {event["type"] for event in replay} == {
        "system", "user_message", "assistant_message",
    }
    assert len(replay) == 36
    assert _replay_events([]) == []


def test_live_worker_replay_preserves_final_reply_when_bounded_history_lost_prompt():
    events = [
        {"seq": 101, "type": "tool_result", "data": {"text": "old result"}},
        {"seq": 102, "type": "assistant_message", "data": {"text": "tool call"}},
        {"seq": 103, "type": "tool_result", "data": {"text": "recent result"}},
        {"seq": 104, "type": "assistant_message", "data": {"text": "final answer"}},
    ]

    assert [event["data"]["text"] for event in _replay_events(events)] == [
        "3 messages hidden", "final answer"
    ]


def test_live_worker_replay_omits_slash_commands_without_agent_turns():
    events = [
        {"type": "user_message", "data": {"text": "/usage"}},
        {"type": "system", "data": {"text": "usage"}},
        {"type": "user_message", "data": {"text": "question"}},
        {"type": "iteration_start", "data": {"iteration": 1}},
        {"type": "assistant_message", "data": {"text": "answer"}},
    ]

    assert [event["data"]["text"] for event in _replay_events(events)] == [
        "2 messages hidden", "question", "answer"
    ]


def test_live_worker_replay_starts_after_latest_timeline_reset():
    events = [
        {"type": "user_message", "data": {"text": "discarded"}},
        {"type": "iteration_start", "data": {"iteration": 1}},
        {"type": "assistant_message", "data": {"text": "old answer"}},
        {"type": "timeline_reset", "data": {}},
        {"type": "user_message", "data": {"text": "current", "source": "replay"}},
        {"type": "assistant_message", "data": {"text": "current answer"}},
    ]

    assert [event["data"]["text"] for event in _replay_events(events)] == [
        "current", "current answer"
    ]


def test_live_worker_replay_preserves_steering_and_final_response():
    events = [
        {"type": "user_message", "data": {"text": "question"}},
        {"type": "iteration_start", "data": {"iteration": 1}},
        {"type": "assistant_message", "data": {"text": "tool call"}},
        {"type": "tool_start", "data": {"text": "read_file"}},
        {"type": "tool_result", "data": {"text": "result"}},
        {"type": "user_message", "data": {"text": "steer", "steer": True}},
        {"type": "iteration_start", "data": {"iteration": 2}},
        {"type": "assistant_message", "data": {"text": "final answer"}},
    ]

    assert [event["data"]["text"] for event in _replay_events(events)] == [
        "question", "3 messages hidden", "steer", "final answer"
    ]


def test_live_worker_replay_hydrates_active_stream_deltas():
    events = [
        {"type": "user_message", "data": {"text": "question"}},
        {"type": "iteration_start", "data": {"iteration": 1}},
        {"type": "thinking_delta", "data": {"text": "reasoning"}},
        {"type": "assistant_delta", "data": {"text": "partial answer"}},
    ]

    replay = _replay_events(events)

    assert [(event["type"], event["data"]["text"]) for event in replay] == [
        ("user_message", "question"),
        ("thinking_delta", "reasoning"),
        ("assistant_delta", "partial answer"),
    ]


def test_active_indicator_ignores_completed_historical_status():
    active = {"label": "Executing", "started_at": 123.0}
    assert _active_indicator([
        {"type": "thinking_start", "data": {"label": "Old"}},
        {"type": "thinking_stop", "data": {}},
        {"type": "thinking_start", "data": active},
    ]) == active
    assert _active_indicator([
        {"type": "thinking_start", "data": active},
        {"type": "thinking_stop", "data": {}},
    ]) is None


def test_live_worker_restores_saved_name_when_resuming(monkeypatch):
    worker = Worker.__new__(Worker)
    worker.runtime_id = "runtime"
    worker.record = {
        "name": "",
        "workspace": "/tmp/workspace",
        "options": {"resume": "conversation"},
    }
    worker._make_agent = lambda: SimpleNamespace(
        session_name="",
        model_alias="model",
        _session_id="conversation",
        load_session=lambda _session_id: setattr(worker.agent, "session_name", "saved name") or True,
        _initialize_chat_session=lambda _session_id: None,
        _refresh_slash_commands=lambda: None,
    )
    identities = []
    monkeypatch.setattr(
        live_worker,
        "update_identity",
        lambda runtime_id, **changes: identities.append((runtime_id, changes)) or worker.record | changes,
    )
    monkeypatch.setattr(
        live_worker,
        "update_record",
        lambda _runtime_id, **changes: worker.record | changes,
    )
    monkeypatch.setattr(worker, "_start_hub", lambda: (_ for _ in ()).throw(RuntimeError("stop")))

    with pytest.raises(RuntimeError, match="stop"):
        worker.start()

    assert identities == [("runtime", {
        "name": "saved name",
        "workspace": "/tmp/workspace",
        "conversation_id": "conversation",
    })]
    assert worker.record["name"] == "saved name"


def test_update_identity_renames_and_rejects_live_conflicts(monkeypatch, tmp_path):
    monkeypatch.setattr(live, "LIVE_DIR", tmp_path / "live")
    monkeypatch.setattr(live, "REGISTRY_LOCK", tmp_path / "live" / ".lock")
    first = live.create_record(name="first", workspace=str(tmp_path), options={})
    second = live.create_record(name="second", workspace=str(tmp_path), options={})

    updated = live.update_identity(
        first["runtime_id"], name="renamed", workspace=str(tmp_path), conversation_id="one"
    )
    assert updated["name"] == "renamed"
    with pytest.raises(live.LiveError, match="already exists"):
        live.update_identity(
            second["runtime_id"], name="renamed", workspace=str(tmp_path), conversation_id="two"
        )
    with pytest.raises(live.LiveError, match="already live"):
        live.update_identity(
            second["runtime_id"], name="other", workspace=str(tmp_path), conversation_id="one"
        )
