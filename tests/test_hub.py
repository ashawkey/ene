import inspect
import json
import socket
import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ene import live
from ene.hub import Hub, RemoteSession
from ene.live_worker import Worker
from ene.utils.io import CancellationToken, EventHub, InputBroker, PromptBroker


def make_hub():
    return Hub(token="correct-token")


class FakeWorker:
    """A real worker control listener with a stubbed agent.

    Exercises the actual attach/detach/action protocol the hub speaks, without
    starting a provider-backed agent process.
    """

    def __init__(self, runtime_id="runtime-1", name="w1", workspace="/tmp/ws"):
        worker = Worker.__new__(Worker)
        worker.runtime_id = runtime_id
        worker.token = "worker-token"
        worker.record = {"runtime_id": runtime_id, "workspace": workspace}
        worker.stop_event = threading.Event()
        worker.terminal_lock = threading.Lock()
        worker.terminal_attached = False
        worker.attachment_owner = ""
        worker.connections = set()
        worker.connections_lock = threading.Lock()
        worker.events = EventHub()
        worker.inputs = InputBroker(worker.events)
        worker.prompts = PromptBroker(worker.events)
        worker.cancellation = CancellationToken(worker.events, worker.prompts)
        worker.agent = None
        worker.server = None
        worker._shutdown_lock = threading.Lock()
        worker._shutdown_started = False
        worker._status = lambda: {
            "runtime_id": runtime_id,
            "name": name,
            "workspace": workspace,
            "attached": worker.terminal_attached,
            "attached_by": worker.attachment_owner,
            "process_status": self.process_status,
        }
        self.process_status = ""
        self.worker = worker
        self.events = worker.events
        self.inputs = worker.inputs
        self._server = socket.socket()
        self._server.bind(("127.0.0.1", 0))
        self._server.listen()
        self.record = {
            "runtime_id": runtime_id,
            "name": name,
            "workspace": workspace,
            "model": "m",
            "conversation_id": "conv",
            "status": "ready",
            "port": self._server.getsockname()[1],
            "token": "worker-token",
            "created_at": time.time(),
        }
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while not self.worker.stop_event.is_set():
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(
                target=self.worker._handle, args=(conn,), daemon=True
            ).start()

    @property
    def attached_by(self):
        return self.worker.attachment_owner

    def attach_terminal(self):
        """Take the slot as a terminal would, and return the open socket."""
        sock = live.connect(self.record, "attach", client="terminal")
        live.recv_frame(sock)
        return sock

    def stop_serving(self):
        """Close client connections the way a shutting-down worker does."""
        self.worker.stop_event.set()
        for connection in list(self.worker.connections):
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

    def close(self):
        self.stop_serving()
        self._server.close()


def wait_until(predicate, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def receive_type(sock, event_type, limit=10):
    for _ in range(limit):
        message = sock.receive_json()
        if message.get("type") == event_type:
            return message
    raise AssertionError(f"Did not receive {event_type!r}")


def add_session(hub, session_id="s1", **meta):
    """Register an attached session directly, without a worker behind it."""
    meta.setdefault("title", "proj · model")
    meta.setdefault("cwd", "/proj")
    meta.setdefault("model", "model")
    meta.setdefault("host", "box")
    session = RemoteSession(session_id, meta)
    hub._sessions[session_id] = session
    return session


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ws_header_kwargs(websockets, headers):
    params = inspect.signature(websockets.connect).parameters
    if "additional_headers" in params:
        return {"additional_headers": headers}
    return {"extra_headers": headers}


# -- static / auth (browser surface) ---------------------------------------

def test_hub_binds_all_interfaces_and_uses_hostname_in_browser_url(monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "ene-box")
    hub = make_hub()
    assert hub.host == "0.0.0.0"
    assert hub.url == "http://ene-box:8765"


def test_login_rejects_bad_tokens():
    hub = make_hub()
    with TestClient(hub.app) as client:
        assert client.post("/api/login", json={"token": "wrong"}).status_code == 401
        # Non-ASCII tokens must be a clean 401, not a compare_digest TypeError.
        assert client.post("/api/login", json={"token": "秘密"}).status_code == 401
        response = client.post("/api/login", json={"token": "correct-token"})
        assert response.status_code == 200
        assert response.cookies.get("ene_web_session")
        assert "Secure" not in response.headers["set-cookie"]


def test_sessions_endpoint_requires_auth_and_lists():
    hub = make_hub()
    add_session(hub, "s1")
    with TestClient(hub.app) as client:
        assert client.get("/api/sessions").status_code == 403
        client.post("/api/login", json={"token": "correct-token"})
        listed = client.get("/api/sessions").json()["sessions"]
        assert [s["id"] for s in listed] == ["s1"]


# -- browser websockets ----------------------------------------------------

def test_per_session_state_and_event_replay():
    hub = make_hub()
    session = add_session(hub, "s1")
    # The authoritative state must retain status even after its event falls out
    # of the bounded replay window.
    session.events = EventHub(max_events=1)
    session.ingest({
        "type": "process_status",
        "data": {"running": 1, "finished": 0, "text": "1/1 running processes"},
    })
    session.commands = {"help": "Show help"}
    session.events.publish("system", text="ready")
    with TestClient(hub.app) as client:
        response = client.post("/api/login", json={"token": "correct-token"})
        csrf = response.json()["csrf"]
        with client.websocket_connect(
            "/api/ws?session=s1&after=0", headers={"origin": "http://testserver"}
        ) as sock:
            state = receive_type(sock, "state")
            assert state["csrf"] == csrf
            assert state["session"] == "s1"
            assert state["process_status"] == "1/1 running processes"
            assert state["commands"] == {"help": "Show help"}
            assert state["replay_truncated"] is True
            assert receive_type(sock, "system")["data"]["text"] == "ready"


def test_browser_websockets_answer_heartbeats():
    hub = make_hub()
    add_session(hub, "s1")
    with TestClient(hub.app) as client:
        client.post("/api/login", json={"token": "correct-token"})
        headers = {"origin": "http://testserver"}
        with client.websocket_connect("/api/ws", headers=headers) as control:
            assert receive_type(control, "sessions")["type"] == "sessions"
            control.send_json({"type": "ping"})
            assert receive_type(control, "pong")["type"] == "pong"
            # A malformed frame must not tear down the control channel.
            control.send_text("not json")
            control.send_json({"type": "ping"})
            assert receive_type(control, "pong")["type"] == "pong"
        with client.websocket_connect("/api/ws?session=s1", headers=headers) as session:
            assert receive_type(session, "state")["type"] == "state"
            session.send_json({"type": "ping"})
            assert receive_type(session, "pong")["type"] == "pong"


def test_websocket_requires_same_origin():
    hub = make_hub()
    add_session(hub, "s1")
    with TestClient(hub.app) as client:
        client.post("/api/login", json={"token": "correct-token"})
        with client.websocket_connect(
            "/api/ws?session=s1", headers={"origin": "https://attacker.example"}
        ) as sock:
            with pytest.raises(WebSocketDisconnect) as exc:
                sock.receive_json()
        assert exc.value.code == 4403


# -- RemoteSession derived-state unit --------------------------------------

def test_remote_session_tracks_context_status_for_authoritative_state():
    session = RemoteSession("s1", {})
    session.ingest({
        "type": "context_status",
        "data": {
            "context_tokens": 96_000,
            "context_limit": 128_000,
            "input_tokens": 200_000,
            "output_tokens": 4_000,
            "cached_tokens": 150_000,
        },
    })

    assert session.context_status == {
        "context_tokens": 96_000,
        "context_limit": 128_000,
        "input_tokens": 200_000,
        "output_tokens": 4_000,
        "cached_tokens": 150_000,
    }


def test_remote_session_tracks_latest_indicator_for_authoritative_state():
    session = RemoteSession("s1", {})
    session.ingest({
        "type": "thinking_start",
        "data": {"label": "Batch", "suffix": "0/4 completed"},
    })
    session.ingest({
        "type": "thinking_update",
        "data": {"suffix": "2/4 completed\n├ 3 · c\n└ 4 · d"},
    })
    assert session.active_indicator == {
        "label": "Batch", "suffix": "2/4 completed\n├ 3 · c\n└ 4 · d"
    }

    session.ingest({"type": "thinking_stop", "data": {}})
    assert session.active_indicator is None


def test_remote_session_tracks_commands_for_authoritative_state():
    session = RemoteSession("s1", {})
    session.ingest({
        "type": "commands",
        "data": {"commands": {"help": "Show help", "review": "Skill — Review code"}},
    })
    assert session.commands == {"help": "Show help", "review": "Skill — Review code"}


def test_remote_session_tracks_process_status_for_authoritative_state():
    session = RemoteSession("s1", {})
    session.ingest({
        "type": "process_status",
        "data": {"running": 1, "finished": 2, "text": "1/3 running processes"},
    })
    assert session.process_status == "1/3 running processes"

    session.ingest({
        "type": "process_status",
        "data": {"running": 0, "finished": 3, "text": ""},
    })
    assert session.process_status == ""


def test_remote_session_hydrates_state_from_attach_payload():
    """A browser attaching mid-round must see live state before any new event."""
    session = RemoteSession("s1", {})
    session.hydrate({
        "name": "review",
        "conversation_id": "conv-1",
        "operation_id": "op-1",
        "process_status": "1/2 running processes",
        "context_status": {"context_tokens": 10, "context_limit": 100},
        "active_indicator": {"label": "Executing", "started_at": 5.0},
        "commands": {"help": "Show help", "review": "Skill — Review code"},
        "active_prompt": {
            "id": "p1", "kind": "select", "message": "Pick",
            "choices": ["a", "b"], "default": "a",
        },
        "pending": {"id": "s1", "text": "queued", "source": "terminal"},
    })

    assert session.operation_id == "op-1"
    assert session.process_status == "1/2 running processes"
    assert session.context_status["context_tokens"] == 10
    assert session.active_indicator == {"label": "Executing", "started_at": 5.0}
    assert session.commands == {"help": "Show help", "review": "Skill — Review code"}
    assert session.prompt["choices"] == ["a", "b"]
    assert session.pending["text"] == "queued"
    assert session.summary()["name"] == "review"
    assert session.summary()["conversation_id"] == "conv-1"
    assert session.summary()["state"] == "working"


# -- live worker attachment (real loopback protocol) -----------------------

def test_hub_attaches_streams_and_detaches_a_worker():
    worker = FakeWorker()
    worker.process_status = "1/1 running processes"
    worker.events.publish("system", text="worker online")
    hub = make_hub()
    hub._records = [worker.record]
    try:
        session = hub.attach_session("runtime-1")

        # Attach payload hydrates derived state, and replay reaches browsers.
        assert session.process_status == "1/1 running processes"
        assert worker.attached_by == "web"
        assert wait_until(lambda: session.events.latest_seq >= 1)
        assert [event.type for event in session.events.snapshot()] == ["system"]

        # Live events keep flowing to the hub-local stream.
        worker.events.publish("assistant_message", text="hello")
        assert wait_until(
            lambda: any(
                event.type == "assistant_message"
                for event in session.events.snapshot()
            )
        )

        # A browser action reaches the worker's broker, tagged as web input.
        import asyncio

        forwarded = asyncio.new_event_loop().run_until_complete(
            hub._forward_to_worker(
                "runtime-1", {"type": "submit", "text": "yo", "action_id": "a1"}
            )
        )
        assert forwarded is True
        assert wait_until(lambda: worker.inputs.pending)
        item = worker.inputs.get_nowait()
        assert (item.text, item.source, item.action_id) == ("yo", "web", "a1")

        # Detaching releases the slot without stopping the worker.
        assert hub.detach_session("runtime-1") is True
        assert wait_until(lambda: worker.attached_by == "")
        assert hub.get_session("runtime-1") is None
        assert not worker.worker.stop_event.is_set()
    finally:
        hub.stop()
        worker.close()


def test_hub_rejects_attaching_a_terminal_owned_session():
    worker = FakeWorker()
    terminal = worker.attach_terminal()
    hub = make_hub()
    hub._records = [worker.record]
    try:
        with pytest.raises(live.LiveBusyError) as exc:
            hub.attach_session("runtime-1")
        assert exc.value.owner == "terminal"
        assert hub.get_session("runtime-1") is None
    finally:
        terminal.close()
        hub.stop()
        worker.close()


def test_terminal_cannot_attach_a_hub_owned_session():
    """The single attachment slot is exclusive in both directions."""
    worker = FakeWorker()
    hub = make_hub()
    hub._records = [worker.record]
    try:
        hub.attach_session("runtime-1")

        with pytest.raises(live.LiveBusyError) as exc:
            live.connect(worker.record, "attach", client="terminal")
        assert exc.value.owner == "web"

        # After the hub detaches, the terminal can take the session over.
        hub.detach_session("runtime-1")
        assert wait_until(lambda: worker.attached_by == "")
        terminal = worker.attach_terminal()
        assert wait_until(lambda: worker.attached_by == "terminal")
        terminal.close()
    finally:
        hub.stop()
        worker.close()


def test_hub_drops_a_session_whose_worker_exits():
    """`/exit` in the browser ends the worker; its tab must disappear."""
    worker = FakeWorker()
    hub = make_hub()
    hub._records = [worker.record]
    try:
        hub.attach_session("runtime-1")
        assert hub.get_session("runtime-1") is not None

        # A stopping worker closes its client connections. `_request_stop` is
        # not called here: its watchdog would terminate the test process.
        worker.stop_serving()

        assert wait_until(lambda: hub.get_session("runtime-1") is None)
    finally:
        hub.stop()
        worker.close()


def test_hub_stop_releases_attached_worker_slots():
    """Ctrl+C on the hub must not reserve sessions until the idle timeout."""
    worker = FakeWorker()
    hub = make_hub()
    hub._records = [worker.record]
    try:
        hub.attach_session("runtime-1")
        assert worker.attached_by == "web"

        hub.stop()

        assert wait_until(lambda: worker.attached_by == "")
    finally:
        worker.close()


def test_rejected_submission_is_reported_to_every_browser():
    """A message the worker refuses must not be acknowledged optimistically."""
    import asyncio

    worker = FakeWorker()
    hub = make_hub()
    hub._records = [worker.record]
    try:
        session = hub.attach_session("runtime-1")
        worker.inputs.submit("already queued", "terminal")

        forwarded = asyncio.new_event_loop().run_until_complete(
            hub._forward_to_worker(
                "runtime-1", {"type": "submit", "text": "second", "action_id": "a2"}
            )
        )

        assert forwarded is True
        published = [event.type for event in session.events.snapshot()]
        assert "submission_rejected" in published
        rejection = next(
            event for event in session.events.snapshot()
            if event.type == "submission_rejected"
        )
        assert rejection.data["action_id"] == "a2"
        assert "already pending" in rejection.data["error"]
    finally:
        hub.stop()
        worker.close()


def test_session_list_merges_attached_and_unattached_records():
    hub = make_hub()
    hub._records = [
        {
            "runtime_id": "detached-1", "name": "other", "workspace": "/w",
            "model": "m", "conversation_id": "c1", "status": "ready",
            "attached": False, "busy": False, "needs_attention": True,
        },
        {
            "runtime_id": "terminal-1", "name": "shell", "workspace": "/w2",
            "model": "m", "conversation_id": "c2", "status": "ready",
            "attached": True, "attached_by": "terminal", "busy": True,
        },
    ]

    listed = {item["id"]: item for item in hub._session_list()}

    assert listed["detached-1"]["attached_by"] == ""
    assert listed["detached-1"]["state"] == "done"
    assert listed["terminal-1"]["attached_by"] == "terminal"
    assert listed["terminal-1"]["state"] == "working"


# -- session management API ------------------------------------------------

def login(client):
    return client.post("/api/login", json={"token": "correct-token"}).json()["csrf"]


def test_mutating_endpoints_require_a_csrf_token():
    hub = make_hub()
    with TestClient(hub.app) as client:
        csrf = login(client)
        for path in (
            "/api/sessions",
            "/api/sessions/x/attach",
            "/api/sessions/x/detach",
        ):
            assert client.post(path, json={}).status_code == 403
            assert client.post(
                path, json={}, headers={"x-csrf-token": "wrong"}
            ).status_code == 403
        # The real token gets past authentication (and fails on its merits).
        assert client.post(
            "/api/sessions", json={}, headers={"x-csrf-token": csrf}
        ).status_code == 400


def test_create_session_validates_the_working_directory(tmp_path):
    hub = make_hub()
    with TestClient(hub.app) as client:
        csrf = login(client)
        headers = {"x-csrf-token": csrf}

        missing = client.post("/api/sessions", json={"cwd": ""}, headers=headers)
        assert missing.status_code == 400
        assert "working directory" in missing.json()["detail"]

        relative = client.post(
            "/api/sessions", json={"cwd": "relative/path"}, headers=headers
        )
        assert relative.status_code == 400
        assert "absolute" in relative.json()["detail"]

        file_path = tmp_path / "a-file"
        file_path.write_text("x", encoding="utf-8")
        not_dir = client.post(
            "/api/sessions", json={"cwd": str(file_path)}, headers=headers
        )
        assert not_dir.status_code == 400
        assert "directory" in not_dir.json()["detail"]


def test_create_session_reports_worker_failures(monkeypatch, tmp_path):
    hub = make_hub()
    monkeypatch.setattr(
        "ene.hub.start_session",
        lambda **_kwargs: (_ for _ in ()).throw(
            live.LiveError("A live session named 'dup' already exists")
        ),
    )
    with TestClient(hub.app) as client:
        response = client.post(
            "/api/sessions",
            json={"cwd": str(tmp_path), "name": "dup"},
            headers={"x-csrf-token": login(client)},
        )

    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


def test_create_session_starts_and_attaches_a_worker(monkeypatch, tmp_path):
    worker = FakeWorker(workspace=str(tmp_path))
    hub = make_hub()
    started = {}

    def fake_start_session(*, name, workspace, options):
        started.update(name=name, workspace=workspace, options=options)
        hub._records = [worker.record]
        return worker.record

    monkeypatch.setattr("ene.hub.start_session", fake_start_session)
    monkeypatch.setattr("ene.hub.list_records", lambda: [worker.record])
    try:
        with TestClient(hub.app) as client:
            response = client.post(
                "/api/sessions",
                json={
                    "cwd": str(tmp_path), "name": "review", "model": "gpt",
                    "persona": "coder", "reasoning_effort": "high",
                    "resume": "conv-7",
                },
                headers={"x-csrf-token": login(client)},
            )

        assert response.status_code == 200
        assert response.json()["session"]["attached_by"] == "web"
        assert started["name"] == "review"
        assert started["workspace"] == str(tmp_path)
        assert started["options"]["model"] == "gpt"
        assert started["options"]["persona"] == "coder"
        assert started["options"]["reasoning_effort"] == "high"
        assert started["options"]["resume"] == "conv-7"
        assert worker.attached_by == "web"
    finally:
        hub.stop()
        worker.close()


def test_attach_endpoint_conflicts_with_a_terminal_owner(monkeypatch):
    worker = FakeWorker()
    terminal = worker.attach_terminal()
    hub = make_hub()
    monkeypatch.setattr("ene.hub.list_records", lambda: [worker.record])
    try:
        with TestClient(hub.app) as client:
            headers = {"x-csrf-token": login(client)}
            conflict = client.post("/api/sessions/runtime-1/attach", headers=headers)
            missing = client.post("/api/sessions/nope/attach", headers=headers)

        assert conflict.status_code == 409
        assert "attached" in conflict.json()["detail"]
        assert missing.status_code == 404
    finally:
        terminal.close()
        hub.stop()
        worker.close()


def test_attach_and_detach_endpoints_round_trip(monkeypatch):
    worker = FakeWorker()
    hub = make_hub()
    monkeypatch.setattr("ene.hub.list_records", lambda: [worker.record])
    try:
        with TestClient(hub.app) as client:
            headers = {"x-csrf-token": login(client)}
            attached = client.post("/api/sessions/runtime-1/attach", headers=headers)
            assert attached.status_code == 200
            assert worker.attached_by == "web"

            detached = client.post("/api/sessions/runtime-1/detach", headers=headers)
            assert detached.status_code == 200
            assert wait_until(lambda: worker.attached_by == "")

            # Detaching twice is a clean 404, not a crash.
            again = client.post("/api/sessions/runtime-1/detach", headers=headers)
            assert again.status_code == 404
    finally:
        hub.stop()
        worker.close()


def test_fs_endpoint_lists_only_directories(tmp_path):
    (tmp_path / "project").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    hub = make_hub()
    with TestClient(hub.app) as client:
        assert client.get("/api/fs").status_code == 403
        login(client)
        listing = client.get("/api/fs", params={"path": str(tmp_path)}).json()

        assert listing["path"] == str(tmp_path)
        assert listing["parent"] == str(tmp_path.parent)
        names = {entry["name"]: entry for entry in listing["entries"]}
        assert set(names) == {"project", ".hidden"}
        assert names[".hidden"]["hidden"] is True
        assert names["project"]["path"] == str(tmp_path / "project")

        assert client.get("/api/fs", params={"path": "relative"}).status_code == 400
        assert client.get(
            "/api/fs", params={"path": str(tmp_path / "notes.txt")}
        ).status_code == 400


def test_conversations_endpoint_marks_live_conversations(tmp_path):
    sessions = tmp_path / ".ene" / "sessions" / "20260101_000000"
    sessions.mkdir(parents=True)
    (sessions / "history.jsonl").write_text("", encoding="utf-8")
    hub = make_hub()
    hub._records = [{
        "runtime_id": "r1", "name": "live one", "workspace": str(tmp_path),
        "model": "m", "conversation_id": "20260101_000000", "status": "ready",
        "attached": False,
    }]
    with TestClient(hub.app) as client:
        login(client)
        listed = client.get(
            "/api/conversations", params={"cwd": str(tmp_path)}
        ).json()["conversations"]

    assert [item["id"] for item in listed] == ["20260101_000000"]
    assert listed[0]["live"] is True


def test_options_endpoint_reports_models_and_personas(monkeypatch, tmp_path):
    from ene.config import conf

    monkeypatch.setitem(conf, "openai", {"gpt": {"model": "gpt-x"}, "fast": {}})
    hub = make_hub()
    with TestClient(hub.app) as client:
        login(client)
        options = client.get("/api/options", params={"cwd": str(tmp_path)}).json()

    assert options["models"] == ["gpt", "fast"]
    assert options["default_model"] == "gpt"
    assert "coder" in options["personas"]
    assert "high" in options["reasoning_efforts"]


def test_workspaces_endpoint_lists_recent_directories():
    hub = make_hub()
    hub._records = [
        {"runtime_id": "r1", "workspace": "/older", "created_at": 1},
        {"runtime_id": "r2", "workspace": "/newer", "created_at": 2},
    ]
    with TestClient(hub.app) as client:
        login(client)
        listed = client.get("/api/workspaces").json()["workspaces"]

    assert listed[:2] == ["/newer", "/older"]


def test_session_preview_tracks_the_last_user_message():
    """Cards fall back to the last request when a session has no name."""
    session = RemoteSession("s1", {})
    session.hydrate({"last_user_message": "resumed request"})
    assert session.summary()["preview"] == "resumed request"

    session.ingest({"type": "user_message", "data": {"text": "  fix   the\nparser  "}})

    # Whitespace is collapsed so a multi-line request stays a single line.
    assert session.summary()["preview"] == "fix the parser"


def test_session_list_reports_previews_for_detached_records():
    hub = make_hub()
    hub._records = [{
        "runtime_id": "detached-1", "name": "", "workspace": "/w", "model": "m",
        "conversation_id": "c1", "status": "ready", "attached": False,
        "last_user_message": "x" * 400,
    }]

    listed = hub._session_list()

    assert len(listed[0]["preview"]) == 160


def test_attached_session_reports_done_when_idle():
    """An attached session that finished its round must be reviewable.

    The summary previously reported only working/waiting, so the "needs
    review" state was unreachable for sessions this hub owns.
    """
    session = RemoteSession("s1", {})
    assert session.summary()["state"] == "done"

    session.ingest({"type": "operation_start", "data": {"id": "op-1"}})
    assert session.summary()["state"] == "working"

    session.ingest({"type": "operation_end", "data": {"id": "op-1"}})
    assert session.summary()["state"] == "done"

    session.ingest({"type": "pending_set", "data": {"id": "p1", "text": "queued"}})
    assert session.summary()["state"] == "waiting"

    session.ingest({"type": "pending_cleared", "data": {"id": "p1"}})
    assert session.summary()["state"] == "done"
