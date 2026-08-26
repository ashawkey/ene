"""Persistent local worker discovery and authenticated loopback protocol."""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from filelock import FileLock

from ene.utils.process import process_exited


LIVE_DIR = Path.home() / ".ene" / "live"
REGISTRY_LOCK = LIVE_DIR / ".lock"
CONNECT_TIMEOUT = 1.0
REQUEST_TIMEOUT = 5.0
START_TIMEOUT = 15.0
STOP_TIMEOUT = 20.0
STOP_RECORD_GRACE = 30.0
MAX_FRAME_BYTES = 2 * 1024 * 1024

# Attached terminals send a heartbeat ("ping") frame every
# TERMINAL_PING_INTERVAL seconds. A force-killed shell leaves a half-open
# loopback connection that may never signal EOF to the worker, so the worker
# treats an attachment that stays silent for TERMINAL_IDLE_TIMEOUT seconds as
# dead and releases the session instead of reserving it forever.
TERMINAL_PING_INTERVAL = 5.0
TERMINAL_IDLE_TIMEOUT = 20.0
# A reattach that lands inside the previous terminal's idle window waits for
# the worker to release it rather than reporting a conflict that resolves
# itself moments later.
ATTACH_WAIT_TIMEOUT = TERMINAL_IDLE_TIMEOUT + 5.0


class LiveError(RuntimeError):
    pass


class LiveBusyError(LiveError):
    """Another client holds the session's single attachment slot.

    ``owner`` is the client kind holding it (``"terminal"`` or ``"web"``). Only
    a terminal slot can self-release after its heartbeat deadline, so waiting
    out the conflict is worthwhile only for that owner.
    """

    def __init__(self, message: str, owner: str = "terminal"):
        super().__init__(message)
        self.owner = owner


def _ensure_live_dir() -> Path:
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        LIVE_DIR.chmod(0o700)
    return LIVE_DIR


def record_path(runtime_id: str) -> Path:
    return _ensure_live_dir() / f"{runtime_id}.json"


def unlink_record(path: Path) -> None:
    """Remove a live-session record and its worker startup log."""
    path.unlink(missing_ok=True)
    path.with_suffix(".log").unlink(missing_ok=True)


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    staging.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    if os.name == "posix":
        staging.chmod(0o600)
    os.replace(staging, path)


def read_record(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def update_record(runtime_id: str, **changes: Any) -> dict[str, Any]:
    path = record_path(runtime_id)
    with FileLock(str(REGISTRY_LOCK)):
        record = read_record(path)
        if record is None:
            raise LiveError(f"Live session disappeared: {runtime_id}")
        record.update(changes)
        _atomic_write(path, record)
        return record


def update_identity(
    runtime_id: str,
    *,
    name: str,
    workspace: str,
    conversation_id: str,
) -> dict[str, Any]:
    """Atomically validate and update a live worker's public identity."""
    name = validate_name(name) if name else ""
    workspace = str(Path(workspace).resolve())
    path = record_path(runtime_id)
    with FileLock(str(REGISTRY_LOCK)):
        record = read_record(path)
        if record is None:
            raise LiveError(f"Live session disappeared: {runtime_id}")
        for other_path in _ensure_live_dir().glob("*.json"):
            if other_path == path:
                continue
            other = read_record(other_path)
            if other is None:
                continue
            if name and other.get("name") == name:
                raise LiveError(f"A live session named '{name}' already exists")
            if (
                conversation_id
                and other.get("workspace") == workspace
                and other.get("conversation_id") == conversation_id
            ):
                raise LiveError(f"Conversation '{conversation_id}' is already live")
        record.update(
            name=name,
            workspace=workspace,
            conversation_id=conversation_id,
        )
        _atomic_write(path, record)
        return record


def send_frame(sock: socket.socket, message: dict[str, Any]) -> None:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_FRAME_BYTES:
        raise LiveError("Live-session message is too large")
    sock.sendall(len(payload).to_bytes(4, "big") + payload)


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise EOFError
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_frame(sock: socket.socket) -> dict[str, Any]:
    size = int.from_bytes(_recv_exact(sock, 4), "big")
    if size <= 0 or size > MAX_FRAME_BYTES:
        raise LiveError("Invalid live-session frame")
    value = json.loads(_recv_exact(sock, size))
    if not isinstance(value, dict):
        raise LiveError("Invalid live-session message")
    return value


def connect(record: dict[str, Any], kind: str, **data: Any) -> socket.socket:
    sock: socket.socket | None = None
    try:
        sock = socket.create_connection(
            ("127.0.0.1", int(record.get("port", 0))), timeout=CONNECT_TIMEOUT
        )
        sock.settimeout(REQUEST_TIMEOUT)
        send_frame(sock, {"type": kind, "token": record.get("token", ""), **data})
        reply = recv_frame(sock)
        if not reply.get("ok"):
            error = str(reply.get("error", "Live-session request rejected"))
            if reply.get("code") == "attached":
                owner = reply.get("owner")
                raise LiveBusyError(
                    error, owner if owner in {"terminal", "web"} else "terminal"
                )
            raise LiveError(error)
        return sock
    except LiveError:
        if sock is not None:
            sock.close()
        raise
    except (OSError, EOFError, ValueError, json.JSONDecodeError) as exc:
        if sock is not None:
            sock.close()
        raise LiveError("Could not connect to the live session") from exc


def probe(record: dict[str, Any]) -> dict[str, Any] | None:
    try:
        with connect(record, "status") as sock:
            return recv_frame(sock)
    except (OSError, EOFError, ValueError, LiveError, json.JSONDecodeError):
        return None


def list_records(*, clean: bool = True) -> list[dict[str, Any]]:
    root = _ensure_live_dir()
    records: list[dict[str, Any]] = []
    with FileLock(str(REGISTRY_LOCK)):
        paths = list(root.glob("*.json"))
    for path in paths:
        record = read_record(path)
        if record is None:
            if clean:
                unlink_record(path)
            continue
        status = probe(record) if record.get("status") == "ready" else None
        if status is None and record.get("status") == "starting":
            # Do not reap another CLI's worker while it is still starting.
            if time.time() - float(record.get("created_at", 0)) < START_TIMEOUT:
                records.append(record)
                continue
        if status is None and record.get("status") == "stopping":
            # The control listener closes before agent and managed-process
            # cleanup finishes. Keep its identity reserved during that gap.
            if time.time() - float(record.get("stopping_at", 0)) < STOP_RECORD_GRACE:
                records.append(record)
                continue
        if status is None:
            if clean:
                with FileLock(str(REGISTRY_LOCK)):
                    unlink_record(path)
            continue
        record.update(status.get("session", {}))
        records.append(record)
    return sorted(records, key=lambda item: float(item.get("created_at", 0)))


def resolve(identifier: str, *, fuzzy_name: bool = False) -> dict[str, Any]:
    identifier = identifier.strip()
    records = list_records()
    exact = [r for r in records if r.get("name") == identifier or r.get("runtime_id") == identifier]
    if len(exact) == 1:
        record = exact[0]
        if record.get("status", "ready") != "ready":
            raise LiveError(f"Live session is still {record.get('status', 'starting')}: {identifier}")
        return record
    prefixes = [
        r for r in records
        if str(r.get("runtime_id", "")).startswith(identifier)
        or (fuzzy_name and str(r.get("name", "")).startswith(identifier))
    ]
    if len(prefixes) == 1:
        record = prefixes[0]
        if record.get("status", "ready") != "ready":
            raise LiveError(f"Live session is still {record.get('status', 'starting')}: {identifier}")
        return record
    if not exact and not prefixes:
        raise LiveError(f"Live session not found: {identifier}")
    raise LiveError(f"Ambiguous live session: {identifier}")


def find_conversation(workspace: str, conversation_id: str) -> dict[str, Any] | None:
    canonical = str(Path(workspace).resolve())
    for record in list_records():
        if record.get("workspace") == canonical and record.get("conversation_id") == conversation_id:
            return record
    return None


def validate_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise LiveError("Session name cannot be empty")
    if len(name) > 80 or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise LiveError("Session name must be at most 80 characters and contain no control characters")
    return name


def create_record(*, name: str, workspace: str, options: dict[str, Any]) -> dict[str, Any]:
    name = validate_name(name) if name else ""
    workspace = str(Path(workspace).resolve())
    runtime_id = uuid.uuid4().hex
    record = {
        "runtime_id": runtime_id,
        "name": name,
        "workspace": workspace,
        "conversation_id": options.get("resume") or "",
        "model": options.get("model", ""),
        "persona": options.get("persona", ""),
        "created_at": time.time(),
        "status": "starting",
        "port": 0,
        "pid": 0,
        "token": secrets.token_urlsafe(32),
        "options": options,
    }
    # Reap dead records before checking reservations. Authentication proves
    # liveness; a reused PID or stale file cannot reserve a name forever.
    list_records(clean=True)
    with FileLock(str(REGISTRY_LOCK)):
        for existing in list(_ensure_live_dir().glob("*.json")):
            other = read_record(existing)
            if other is None:
                continue
            if name and other.get("name") == name:
                raise LiveError(f"A live session named '{name}' already exists")
            conversation_id = record.get("conversation_id")
            if (
                conversation_id
                and other.get("workspace") == workspace
                and other.get("conversation_id") == conversation_id
            ):
                raise LiveError(f"Conversation '{conversation_id}' is already live")
        _atomic_write(record_path(runtime_id), record)
    return record


def launch_worker(record: dict[str, Any]) -> dict[str, Any]:
    path = record_path(str(record["runtime_id"]))
    log_path = path.with_suffix(".log")
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        )
    else:
        kwargs["start_new_session"] = True
    with log_path.open("ab", buffering=0) as log:
        subprocess.Popen(
            [sys.executable, "-m", "ene.live_worker", str(path)],
            cwd=record["workspace"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            close_fds=True,
            **kwargs,
        )
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        current = read_record(path)
        if current is None:
            raise LiveError("Worker exited during startup")
        if current.get("status") == "error":
            unlink_record(path)
            raise LiveError(str(current.get("error", "Worker failed to start")))
        if current.get("status") == "ready" and probe(current) is not None:
            return current
        time.sleep(0.05)
    raise LiveError(f"Worker did not become ready; see {log_path}")


def start_session(*, name: str, workspace: str, options: dict[str, Any]) -> dict[str, Any]:
    resume = options.get("resume")
    if resume:
        existing = find_conversation(workspace, resume)
        if existing is not None:
            return existing
    record = create_record(name=name, workspace=workspace, options=options)
    try:
        return launch_worker(record)
    except Exception:
        unlink_record(record_path(str(record["runtime_id"])))
        raise


def kill_session(record: dict[str, Any]) -> None:
    try:
        pid = int(record.get("pid", 0))
    except (TypeError, ValueError):
        pid = 0
    try:
        with connect(record, "kill") as sock:
            recv_frame(sock)
    except LiveError:
        raise
    except (OSError, EOFError, ValueError, json.JSONDecodeError) as exc:
        raise LiveError("Lost connection while stopping the live session") from exc

    if not process_exited(pid, STOP_TIMEOUT):
        raise LiveError("Session did not stop cleanly")

    # A watchdog exit cannot run worker cleanup. Remove only the same worker's
    # record; a replacement at this path must remain untouched.
    path = record_path(str(record.get("runtime_id", "")))
    with FileLock(str(REGISTRY_LOCK)):
        current = read_record(path)
        if current is not None and current.get("token") == record.get("token"):
            unlink_record(path)


@dataclass
class LiveStatus:
    runtime_id: str
    name: str
    conversation_id: str
    workspace: str
    model: str
    attached: bool
    attached_by: str
    busy: bool
