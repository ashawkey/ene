import json
import os
import shlex
import sys
import threading
import time

import pytest

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ene.hub import terminal as terminal_module
from ene.hub.server import Hub, RemoteSession
from ene.hub.terminal import TerminalError, TerminalManager

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX pseudo-terminals only")

ORIGIN = {"origin": "http://testserver"}


@pytest.fixture(autouse=True)
def plain_shell(monkeypatch):
    # A predictable, quiet shell regardless of the developer's login setup.
    monkeypatch.setenv("SHELL", "/bin/sh")
    monkeypatch.setenv("PS1", "$ ")
    monkeypatch.setenv("ENV", "")


def wait_until(predicate, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class Collector:
    def __init__(self):
        self.data = bytearray()
        self.exit = None

    def __call__(self, kind, payload):
        if kind == "data":
            self.data += payload
        else:
            self.exit = payload


# -- manager -------------------------------------------------------------------

def test_terminal_runs_a_shell_in_the_workspace(tmp_path):
    manager = TerminalManager()
    terminal = manager.create("s1", str(tmp_path), 100, 30)
    try:
        out = Collector()
        terminal.subscribe(out)
        terminal.write(b"pwd; stty size; echo done-$((40+2))\n")
        assert wait_until(lambda: b"done-42" in out.data)
        text = out.data.decode(errors="replace")
        assert str(tmp_path) in text
        assert "30 100" in text
        terminal.resize(120, 40)
        terminal.write(b"stty size\n")
        assert wait_until(lambda: b"40 120" in out.data)
        assert manager.list("s1")[0]["title"] == "Terminal 1"
        assert manager.list("other") == []
    finally:
        manager.close_all(timeout=5)


def test_new_subscribers_replay_scrollback_and_see_exit(tmp_path):
    manager = TerminalManager()
    terminal = manager.create("s1", str(tmp_path))
    first = Collector()
    terminal.subscribe(first)
    terminal.write(b"echo marker-one\n")
    assert wait_until(lambda: b"marker-one\r\n" in first.data)
    snapshot, alive = terminal.subscribe(Collector())
    assert alive and b"marker-one" in snapshot
    terminal.write(b"exit 3\n")
    assert terminal.wait(5)
    assert first.exit == 3
    assert manager.list("s1")[0] == {**manager.list("s1")[0], "alive": False, "exit_code": 3}
    # A finished terminal stays listed until removed.
    assert manager.remove("s1", terminal.id)
    assert manager.list("s1") == []


def test_scrollback_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(terminal_module, "SCROLLBACK_BYTES", 2048)
    manager = TerminalManager()
    terminal = manager.create("s1", str(tmp_path))
    try:
        terminal.subscribe(Collector())
        terminal.write(b"i=0; while [ $i -lt 400 ]; do echo line-$i; i=$((i+1)); done; echo end\n")
        assert wait_until(lambda: b"end" in terminal.subscribe(Collector())[0][-64:])
        snapshot, _ = terminal.subscribe(Collector())
        assert len(snapshot) <= 2048
        assert b"line-0\r\n" not in snapshot
    finally:
        manager.close_all(timeout=5)


def test_close_session_hangs_up_its_terminals_only(tmp_path):
    manager = TerminalManager()
    mine = manager.create("s1", str(tmp_path))
    theirs = manager.create("s2", str(tmp_path))
    try:
        mine.write(b"sleep 60\n")
        manager.close_session("s1")
        assert mine.wait(5)
        assert theirs.alive
        assert manager.list("s1") == []
        assert len(manager.list("s2")) == 1
    finally:
        manager.close_all(timeout=5)
    assert theirs.wait(5)


def test_terminal_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(terminal_module, "MAX_PER_SESSION", 1)
    manager = TerminalManager()
    try:
        manager.create("s1", str(tmp_path))
        with pytest.raises(TerminalError) as exc:
            manager.create("s1", str(tmp_path))
        assert exc.value.status == 429
        with pytest.raises(TerminalError):
            manager.create("s2", str(tmp_path / "missing"))
    finally:
        manager.close_all(timeout=5)



def raw_program(tmp_path, monkeypatch, body):
    """Use, as the "shell", a program that puts its terminal in raw mode
    (like a full-screen app), prints ``ready`` and then runs *body*."""
    script = tmp_path / "raw-shell"
    code = "import os, sys, time, tty\ntty.setraw(0)\nprint('ready', flush=True)\n" + body
    script.write_text(f"#!/bin/sh\nexec {sys.executable} -c {shlex.quote(code)}\n")
    script.chmod(0o755)
    monkeypatch.setenv("SHELL", str(script))


def test_writes_never_block_when_the_program_stops_reading(tmp_path, monkeypatch):
    """Input to a program that is not reading queues instead of parking the
    hub thread that writes it (which closing the terminal did not release)."""
    raw_program(tmp_path, monkeypatch, "time.sleep(60)\n")
    manager = TerminalManager()
    terminal = manager.create("s1", str(tmp_path), 80, 24)
    try:
        out = Collector()
        terminal.subscribe(out)
        assert wait_until(lambda: b"ready" in out.data)
        writer = threading.Thread(
            target=lambda: [terminal.write(b"x" * 1024) for _ in range(512)], daemon=True
        )
        writer.start()
        writer.join(timeout=5)
        assert not writer.is_alive(), "a write blocked on a program that is not reading"
    finally:
        manager.close_all(timeout=5)


def test_queued_input_reaches_the_program_once_it_reads(tmp_path, monkeypatch):
    raw_program(tmp_path, monkeypatch, (
        "time.sleep(1)\n"
        "total = 0\n"
        "while total < 300000:\n"
        "    total += len(os.read(0, 65536))\n"
        "print(f'got {total}', flush=True)\n"
        "time.sleep(60)\n"
    ))
    manager = TerminalManager()
    terminal = manager.create("s1", str(tmp_path), 80, 24)
    try:
        out = Collector()
        terminal.subscribe(out)
        assert wait_until(lambda: b"ready" in out.data)
        for _ in range(300):
            terminal.write(b"y" * 1000)
        assert wait_until(lambda: b"got 300000" in out.data, timeout=10)
    finally:
        manager.close_all(timeout=5)

# -- hub endpoints --------------------------------------------------------------

def make_hub(tmp_path, session_id="s1"):
    hub = Hub(token="correct-token")
    hub._sessions[session_id] = RemoteSession(
        session_id, {"title": "proj", "cwd": str(tmp_path), "model": "m", "host": "box"}
    )
    return hub


def login(client):
    return client.post("/api/login", json={"token": "correct-token"}).json()["csrf"]


def receive_until(sock, needle, limit=200):
    data = bytearray()
    for _ in range(limit):
        message = sock.receive()
        if message.get("bytes"):
            data += message["bytes"]
            if needle in data:
                return bytes(data)
    raise AssertionError(f"{needle!r} not received: {bytes(data)[-200:]!r}")


def test_terminal_endpoints_require_login_and_csrf(tmp_path):
    hub = make_hub(tmp_path)
    with TestClient(hub.app) as client:
        assert client.get("/api/sessions/s1/terminals").status_code == 403
        csrf = login(client)
        assert client.post("/api/sessions/s1/terminals", json={}).status_code == 403
        assert client.post("/api/sessions/s1/terminals/x/close", json={}).status_code == 403
        headers = {"x-csrf-token": csrf}
        assert client.post("/api/sessions/nope/terminals", json={}, headers=headers).status_code == 409
        assert client.post("/api/sessions/s1/terminals/x/close", json={}, headers=headers).status_code == 404
    hub.terminals.close_all(timeout=5)


def test_terminal_websocket_round_trip_and_close(tmp_path):
    hub = make_hub(tmp_path)
    try:
        with TestClient(hub.app) as client:
            headers = {"x-csrf-token": login(client)}
            created = client.post(
                "/api/sessions/s1/terminals", json={"cols": 90, "rows": 20}, headers=headers
            ).json()["terminal"]
            listing = client.get("/api/sessions/s1/terminals").json()
            assert listing["supported"] is True
            assert [t["id"] for t in listing["terminals"]] == [created["id"]]

            url = f"/api/sessions/s1/terminals/{created['id']}/ws"
            with client.websocket_connect(url, headers=ORIGIN) as sock:
                sock.send_json({"type": "ping"})
                sock.send_json({"type": "resize", "cols": 70, "rows": 15})
                sock.send_bytes(b"stty size; echo hello-$((6*7))\n")
                assert b"15 70" in receive_until(sock, b"hello-42\r\n")

            # A reconnect replays what the shell printed meanwhile.
            with client.websocket_connect(url, headers=ORIGIN) as sock:
                assert b"hello-42" in receive_until(sock, b"hello-42\r\n")
                sock.send_bytes(b"exit 5\n")
                for _ in range(200):
                    message = sock.receive()
                    if message.get("text") and '"exit"' in message["text"]:
                        assert json.loads(message["text"]) == {"type": "exit", "code": 5}
                        break
                else:
                    raise AssertionError("no exit frame")

            assert client.post(
                f"/api/sessions/s1/terminals/{created['id']}/close", json={}, headers=headers
            ).json() == {"ok": True}
            assert client.get("/api/sessions/s1/terminals").json()["terminals"] == []
    finally:
        hub.terminals.close_all(timeout=5)


def test_terminal_websocket_rejects_foreign_origin_and_unknown_ids(tmp_path):
    hub = make_hub(tmp_path)
    try:
        with TestClient(hub.app) as client:
            headers = {"x-csrf-token": login(client)}
            created = client.post("/api/sessions/s1/terminals", json={}, headers=headers).json()["terminal"]
            url = f"/api/sessions/s1/terminals/{created['id']}/ws"
            with client.websocket_connect(url, headers={"origin": "https://attacker.example"}) as sock:
                with pytest.raises(WebSocketDisconnect) as exc:
                    sock.receive_bytes()
            assert exc.value.code == 4400
            # The terminal belongs to s1, not to another session.
            with client.websocket_connect(f"/api/sessions/s2/terminals/{created['id']}/ws", headers=ORIGIN) as sock:
                with pytest.raises(WebSocketDisconnect) as exc:
                    sock.receive_bytes()
            assert exc.value.code == 4404
    finally:
        hub.terminals.close_all(timeout=5)


def test_detaching_a_session_closes_its_terminals(tmp_path):
    hub = make_hub(tmp_path)
    terminal = hub.terminals.create("s1", str(tmp_path))
    hub.refresh_records = lambda: []
    assert hub.detach_session("s1")
    assert terminal.wait(5)
    assert hub.terminals.list("s1") == []


def test_unsupported_platform_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(terminal_module, "SUPPORTED", False)
    with pytest.raises(TerminalError) as exc:
        TerminalManager().create("s1", str(tmp_path))
    assert exc.value.status == 501


def test_shell_falls_back_when_shell_env_is_unusable(monkeypatch):
    monkeypatch.setenv("SHELL", "relative-shell")
    assert os.path.isabs(terminal_module._shell())
