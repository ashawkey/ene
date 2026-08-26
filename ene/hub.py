"""Standalone web hub for ene live sessions.

A single ``ene hub`` process owns the public port (the one exposed via a
Cloudflare tunnel), serves the browser UI, and manages live sessions directly:
it can start new workers, attach to existing ones, and detach from them again.

The hub is a *client* of each live worker, exactly like a terminal: attaching
takes the worker's single attachment slot over the framed loopback protocol in
:mod:`ene.live` (see :class:`~ene.hublink.WorkerLink`). A session therefore has
exactly one owner — a terminal or the hub, never both.

Client surface: browsers authenticate with the shared token (``/api/login``),
call the ``/api/*`` endpoints to list, create, attach, and detach sessions, and
open ``/api/ws`` — either as a control channel (session list) or, with a
``session=<id>`` query param, as a per-session event stream.

Note: no ``from __future__ import annotations`` here — FastAPI must evaluate
endpoint annotations eagerly, and ``fastapi`` types are only imported inside
``_create_app``.
"""

import asyncio
import hmac
import json
import os
import secrets
import socket
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from starlette.websockets import WebSocketDisconnect

from ene.hublink import WorkerLink
from ene.live import (
    LiveBusyError,
    LiveError,
    kill_session,
    list_records,
    start_session,
)
from ene.utils.io import EventHub


COOKIE_NAME = "ene_web_session"
SESSION_TTL = 12 * 60 * 60
MAX_SESSIONS = 32               # browser login sessions (not live sessions)
# Concurrent browser websockets. Each open browser page holds one control
# socket plus one socket per attached session (all kept open so tab switching
# is instant), so this must comfortably exceed (sessions + 1) * pages.
MAX_CLIENTS = 128
LOGIN_RATE_WINDOW = 60          # seconds
LOGIN_RATE_LIMIT = 8            # attempts per window per IP
EVENT_WAIT_TIMEOUT = 1.0        # fallback re-check interval for browser readers
PREVIEW_LIMIT = 160             # longest last-user-message preview sent to browsers
MAX_BODY_BYTES = 4096
MAX_ANSWER_BYTES = 128 * 1024
LOOPBACK_HOST = "127.0.0.1"
HUB_BIND_HOST = "0.0.0.0"
DEFAULT_HUB_PORT = 8765
REGISTRY_POLL_INTERVAL = 2.0    # live-session registry refresh period
MAX_CREATE_BODY_BYTES = 8192

# Discovery file: written by the hub so a second ``ene hub`` can detect that
# one is already running on this machine.
HUB_INFO_PATH = Path.home() / ".ene" / "hub.json"


def read_hub_info() -> dict | None:
    """Return the hub info file's contents, or ``None`` if absent/corrupt.

    This is a plain file read with no liveness check — the file can be stale if
    a previous hub crashed without cleaning up. Callers should use
    :func:`discover_hub`; the hub itself uses this to reclaim its own file.
    """
    try:
        info = json.loads(HUB_INFO_PATH.read_text(encoding="utf-8"))
        return info if isinstance(info, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _hub_reachable(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError, OverflowError):
        return False


def discover_hub(port: int = DEFAULT_HUB_PORT) -> dict | None:
    """Return a *reachable* hub's connection info, or ``None``.

    Guards against a stale ``hub.json`` left by a crashed hub: the recorded
    endpoint is probed and ignored if nothing is listening. A cross-platform
    TCP probe is used deliberately — ``os.kill(pid, 0)`` is not a safe liveness
    check on Windows, where it terminates the target process.
    """
    info = read_hub_info()
    if not info:
        return None
    if not _hub_reachable(info.get("host", LOOPBACK_HOST), info.get("port", 0)):
        return None
    return info


def _record_state(record: dict) -> str:
    """Describe a live record the same way the terminal session picker does."""
    if record.get("status", "ready") != "ready":
        return str(record.get("status", "starting"))
    if record.get("busy"):
        return "working"
    if record.get("needs_attention", not record.get("busy")):
        return "done"
    return "waiting"


def _registry_digest(records: list[dict]) -> list[tuple]:
    """Reduce records to the fields the browser session list actually shows."""
    return [
        (
            record.get("runtime_id", ""),
            record.get("name", ""),
            record.get("workspace", ""),
            record.get("model", ""),
            record.get("conversation_id", ""),
            record.get("status", ""),
            bool(record.get("attached")),
            record.get("attached_by", ""),
            record.get("last_user_message", ""),
            _record_state(record),
        )
        for record in records
    ]


class RemoteSession:
    """A live worker currently attached by the hub.

    Holds a hub-local :class:`EventHub` that browsers subscribe to. Incoming
    worker events are *re-published* here (re-sequenced with hub-local seqs),
    so all of the browser-facing replay/reconnect logic is reused unchanged.
    The worker's own seq/stream_id never reach the browser.
    """

    def __init__(self, session_id: str, meta: dict):
        self.id = session_id
        self.meta = meta                 # {title, name, cwd, model}
        self.link: WorkerLink | None = None
        self.events = EventHub()
        # Derived UI state, tracked from the event stream (mirrors what the
        # single-agent server pulls from its brokers).
        self.prompt: dict | None = None
        self.pending: dict | None = None
        self.operation_id: str | None = None
        self.process_status = ""
        self.context_status: dict | None = None
        self.active_indicator: dict | None = None
        self.commands: dict[str, str] = {}
        # Async wakeup for browser readers. Events are published on the event
        # loop (via ingest), so browsers wait on this instead of parking a
        # thread-pool worker per open session.
        self._notify = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None

    def reset(self) -> None:
        """Start a fresh event stream (new stream_id) on (re)attach."""
        self.events = EventHub()
        self.prompt = None
        self.pending = None
        self.operation_id = None
        self.process_status = ""
        self.context_status = None
        self.active_indicator = None
        self.commands = {}

    def touch(self) -> None:
        """Wake browser readers (safe to call from any thread)."""
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._notify.set)
        except RuntimeError:
            # The server loop can close between this check and the call; a
            # wakeup for readers that no longer exist is simply dropped.
            pass

    async def wait_events(self, after_seq: int, timeout: float) -> list:
        """Return events with ``seq > after_seq``, waiting up to *timeout*.

        Uses a clear-then-recheck sequence so a publish that races the wait is
        never lost; the *timeout* is only a fallback re-check, not the norm.
        """
        events = self.events.after(after_seq)
        if events:
            return events
        self._notify.clear()
        events = self.events.after(after_seq)
        if events:
            return events
        try:
            await asyncio.wait_for(self._notify.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        return self.events.after(after_seq)

    def hydrate(self, status: dict) -> None:
        """Seed derived state from a worker's attach payload.

        The worker reports its live state (open prompt, queued message, running
        operation, indicator, context) before replaying history, so a browser
        that connects mid-round sees the truth immediately rather than waiting
        for the next event of each kind.
        """
        operation_id = status.get("operation_id")
        self.operation_id = operation_id if isinstance(operation_id, str) else None
        process_status = status.get("process_status", "")
        self.process_status = process_status if isinstance(process_status, str) else ""
        context_status = status.get("context_status")
        self.context_status = (
            {
                key: int(context_status.get(key, 0))
                for key in (
                    "context_tokens", "context_limit", "input_tokens",
                    "output_tokens", "cached_tokens",
                )
            }
            if isinstance(context_status, dict)
            else None
        )
        indicator = status.get("active_indicator")
        self.active_indicator = dict(indicator) if isinstance(indicator, dict) else None
        commands = status.get("commands")
        self.commands = (
            {
                name: description
                for name, description in commands.items()
                if isinstance(name, str) and isinstance(description, str)
            }
            if isinstance(commands, dict)
            else {}
        )
        prompt = status.get("active_prompt")
        self.prompt = (
            {
                "id": prompt.get("id", ""),
                "kind": prompt.get("kind", "text"),
                "message": prompt.get("message", ""),
                "choices": list(prompt.get("choices", []) or []),
                "default": prompt.get("default", ""),
            }
            if isinstance(prompt, dict)
            else None
        )
        pending = status.get("pending")
        self.pending = (
            {
                "id": pending.get("id", ""),
                "text": pending.get("text", ""),
                "source": pending.get("source", ""),
                "action_id": pending.get("action_id"),
            }
            if isinstance(pending, dict)
            else None
        )
        preview = status.get("last_user_message")
        if isinstance(preview, str) and preview:
            self.meta["preview"] = preview[:PREVIEW_LIMIT]
        name = status.get("name")
        if isinstance(name, str) and name:
            self.meta["name"] = name
            self.meta["title"] = name
        conversation_id = status.get("conversation_id")
        if isinstance(conversation_id, str):
            self.meta["conversation_id"] = conversation_id

    def ingest(self, event: dict) -> bool:
        """Consume one worker event: update derived state and re-publish it."""
        etype = event.get("type", "")
        data = event.get("data", {}) or {}
        if etype == "prompt_open":
            self.prompt = {
                "id": data.get("id", ""),
                "kind": data.get("kind", "text"),
                "message": data.get("message", ""),
                "choices": list(data.get("choices", []) or []),
                "default": data.get("default", ""),
            }
        elif etype == "prompt_resolved":
            self.prompt = None
        elif etype == "pending_set":
            self.pending = {
                "id": data.get("id", ""),
                "text": data.get("text", ""),
                "source": data.get("source", ""),
                "action_id": data.get("action_id"),
            }
        elif etype == "pending_cleared":
            if self.pending is not None and self.pending["id"] == data.get("id"):
                self.pending = None
        elif etype == "operation_start":
            self.operation_id = data.get("id")
        elif etype == "operation_end":
            if self.operation_id == data.get("id"):
                self.operation_id = None
        elif etype == "process_status":
            text = data.get("text", "")
            self.process_status = text if isinstance(text, str) else ""
        elif etype == "thinking_start":
            self.active_indicator = dict(data)
        elif etype == "thinking_update" and self.active_indicator is not None:
            suffix = data.get("suffix")
            if isinstance(suffix, str):
                self.active_indicator["suffix"] = suffix
        elif etype == "thinking_stop":
            self.active_indicator = None
        elif etype == "commands":
            commands = data.get("commands")
            if isinstance(commands, dict):
                self.commands = {
                    name: description
                    for name, description in commands.items()
                    if isinstance(name, str) and isinstance(description, str)
                }
        if etype in {"context_status", "thinking_start"} and isinstance(
            data.get("context_tokens"), (int, float)
        ):
            self.context_status = {
                key: int(data.get(key, 0))
                for key in (
                    "context_tokens", "context_limit", "input_tokens",
                    "output_tokens", "cached_tokens",
                )
            }
        elif etype == "session_meta":
            name = data.get("name")
            title = data.get("title")
            if isinstance(name, str):
                self.meta["name"] = name
            if isinstance(title, str):
                self.meta["title"] = title
        elif etype == "user_message":
            text = data.get("text")
            if isinstance(text, str):
                self.meta["preview"] = " ".join(text.split())[:PREVIEW_LIMIT]
        self.events.publish(etype, **data)
        self.touch()
        return etype == "session_meta"

    def summary(self) -> dict:
        return {
            "id": self.id,
            "title": self.meta.get("title", self.id),
            "name": self.meta.get("name", ""),
            "cwd": self.meta.get("cwd", ""),
            "model": self.meta.get("model", ""),
            "host": self.meta.get("host", ""),
            "conversation_id": self.meta.get("conversation_id", ""),
            "preview": self.meta.get("preview", ""),
            "attached_by": "web",
            # Mirrors the worker's own three-way state (see Worker._status):
            # a round is running, a message is queued, or the turn finished and
            # is waiting to be reviewed.
            "state": (
                "working" if self.operation_id is not None
                else "waiting" if self.pending is not None
                else "done"
            ),
        }


class Hub:
    def __init__(
        self,
        *,
        port: int = 8765,
        token: str | None = None,
        console=None,
    ):
        self.host = HUB_BIND_HOST
        self.browser_host = socket.gethostname()
        self.port = port
        self.token = token or secrets.token_urlsafe(32)
        self.console = console

        # Sessions this hub has attached, keyed by worker runtime_id.
        self._sessions: dict[str, RemoteSession] = {}
        self._registry_lock = threading.Lock()
        # Last polled snapshot of every live session on this machine, attached
        # by this hub or not.
        self._records: list[dict] = []
        self._poll_stop = threading.Event()
        self._poll_thread: threading.Thread | None = None
        # Serializes attach/detach so two concurrent browser requests cannot
        # both open a link to the same worker (the second would take the slot
        # the first just registered, and leak a connection).
        self._attach_lock = threading.Lock()
        # Published on every registry change so control-channel browsers refresh.
        self._control = EventHub()
        self._control_notify = asyncio.Event()
        # The uvicorn event loop, captured at startup; used to wake async
        # browser readers from any thread.
        self._loop: asyncio.AbstractEventLoop | None = None

        # Browser auth state.
        self._logins: dict[str, tuple[float, str]] = {}
        self._login_attempts: dict[str, deque] = defaultdict(deque)
        self._login_lock = threading.Lock()
        self._clients = 0
        self._client_lock = threading.Lock()

        self._thread: threading.Thread | None = None
        self._server = None
        self._lifecycle_lock = threading.Lock()
        self.app = self._create_app()

    @property
    def url(self) -> str:
        return f"http://{self.browser_host}:{self.port}"

    # -- logging ------------------------------------------------------------

    def _log(self, msg: str) -> None:
        # Logging must never break a connection (e.g. a console encoding error).
        if self.console is not None:
            try:
                self.console.system(msg)
            except Exception:
                pass

    def _log_dim(self, msg: str) -> None:
        if self.console is not None:
            try:
                self.console.debug(msg)
            except Exception:
                pass

    # -- registry -----------------------------------------------------------

    def _session_list(self) -> list[dict]:
        """Every live session on this machine, attached by this hub or not.

        Sessions this hub has attached report their live derived state; the
        rest are described from their registry record, which already tracks
        worker-side state and terminal ownership.
        """
        with self._registry_lock:
            attached = {sid: s.summary() for sid, s in self._sessions.items()}
            records = list(self._records)
        listing: list[dict] = []
        seen: set[str] = set()
        for record in records:
            runtime_id = str(record.get("runtime_id", ""))
            seen.add(runtime_id)
            summary = attached.get(runtime_id)
            if summary is not None:
                # Keep the record's workspace/model, which the worker owns.
                summary = {
                    **summary,
                    "cwd": record.get("workspace", summary["cwd"]),
                    "model": record.get("model", summary["model"]),
                }
                listing.append(summary)
                continue
            name = record.get("name") or runtime_id[:8]
            listing.append({
                "id": runtime_id,
                "title": name,
                "name": record.get("name", ""),
                "cwd": record.get("workspace", ""),
                "model": record.get("model", ""),
                "host": "",
                "conversation_id": record.get("conversation_id", ""),
                "preview": str(record.get("last_user_message", ""))[:PREVIEW_LIMIT],
                "attached_by": (
                    record.get("attached_by", "terminal")
                    if record.get("attached")
                    else ""
                ),
                "state": _record_state(record),
            })
        # A session attached by this hub is authoritative even if the poll
        # snapshot predates it.
        for runtime_id, summary in attached.items():
            if runtime_id not in seen:
                listing.append(summary)
        return listing

    def refresh_records(self) -> list[dict]:
        """Re-poll the live-session registry and publish any change.

        ``list_records`` probes each worker over TCP, so this must never run on
        the event loop.
        """
        try:
            records = list_records()
        except Exception:
            # A transient registry read failure must not kill the poller.
            return []
        with self._registry_lock:
            changed = _registry_digest(records) != _registry_digest(self._records)
            self._records = records
        if changed:
            self._notify_control()
        return records

    def _poll_loop(self) -> None:
        while not self._poll_stop.is_set():
            self.refresh_records()
            self._poll_stop.wait(REGISTRY_POLL_INTERVAL)

    # -- attachments --------------------------------------------------------

    def attach_session(self, runtime_id: str) -> RemoteSession:
        """Take the worker's attachment slot and stream it to browsers.

        Raises :class:`~ene.live.LiveBusyError` when a terminal owns the
        session and :class:`~ene.live.LiveError` when it cannot be reached.
        """
        with self._attach_lock:
            return self._attach_session(runtime_id)

    def _attach_session(self, runtime_id: str) -> RemoteSession:
        with self._registry_lock:
            existing = self._sessions.get(runtime_id)
            records = list(self._records)
        if existing is not None:
            return existing
        record = next(
            (r for r in records if str(r.get("runtime_id", "")) == runtime_id), None
        )
        if record is None:
            record = next(
                (
                    r for r in self.refresh_records()
                    if str(r.get("runtime_id", "")) == runtime_id
                ),
                None,
            )
        if record is None:
            raise LiveError(f"Live session not found: {runtime_id}")
        if record.get("status", "ready") != "ready":
            raise LiveError(
                f"Live session is still {record.get('status', 'starting')}"
            )

        name = record.get("name") or runtime_id[:8]
        session = RemoteSession(runtime_id, {
            "title": name,
            "name": record.get("name", ""),
            "cwd": record.get("workspace", ""),
            "model": record.get("model", ""),
            "conversation_id": record.get("conversation_id", ""),
            "preview": str(record.get("last_user_message", ""))[:PREVIEW_LIMIT],
        })
        session._loop = self._loop
        session.link = WorkerLink(
            record,
            on_event=session.ingest,
            on_closed=lambda: self._on_link_closed(runtime_id),
        )
        status = session.link.attach()
        session.hydrate(status)
        with self._registry_lock:
            self._sessions[runtime_id] = session
            count = len(self._sessions)
        self.refresh_records()
        session.touch()
        cwd = record.get("workspace", "")
        self._log(
            f"attached: {name}{f' [{cwd}]' if cwd else ''} "
            f"(session {runtime_id[:8]}, {count} attached)"
        )
        return session

    def detach_session(self, runtime_id: str) -> bool:
        """Release the worker's attachment slot, leaving the session running."""
        with self._attach_lock:
            return self._detach_session(runtime_id)

    def _detach_session(self, runtime_id: str) -> bool:
        with self._registry_lock:
            session = self._sessions.pop(runtime_id, None)
            count = len(self._sessions)
        if session is None:
            return False
        if session.link is not None:
            session.link.detach()
        session.touch()  # unblock browser readers so they re-check
        self._log(
            f"detached: {session.meta.get('title', runtime_id)} "
            f"(session {runtime_id[:8]}, {count} attached)"
        )
        self.refresh_records()
        self._notify_control()
        return True

    def _on_link_closed(self, runtime_id: str) -> None:
        """Drop a session whose worker closed the connection (``/exit``, crash).

        Runs on the link's reader thread, so registry refresh is safe here but
        must stay off the event loop.
        """
        with self._registry_lock:
            session = self._sessions.pop(runtime_id, None)
        if session is None:
            return
        session.touch()
        self._log(
            f"session ended: {session.meta.get('title', runtime_id)} "
            f"(session {runtime_id[:8]})"
        )
        self.refresh_records()
        self._notify_control()

    def create_session(self, options: dict) -> RemoteSession:
        """Start a new live worker and attach to it.

        ``start_session`` blocks until the worker is ready, so callers on the
        event loop must run this in an executor.
        """
        record = start_session(
            name=str(options.get("name", "")),
            workspace=str(options["workspace"]),
            options={
                "model": options.get("model", ""),
                "persona": options.get("persona", ""),
                "verbose": False,
                "stream": True,
                "reasoning_effort": options.get("reasoning_effort") or None,
                "resume": options.get("resume") or None,
            },
        )
        self.refresh_records()
        return self.attach_session(str(record["runtime_id"]))

    def _notify_control(self) -> None:
        self._control.publish("sessions_changed")
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._control_notify.set)
        except RuntimeError:
            # Same shutdown race as RemoteSession.touch.
            pass

    def get_session(self, session_id: str) -> RemoteSession | None:
        with self._registry_lock:
            return self._sessions.get(session_id)

    # -- app ----------------------------------------------------------------

    def _create_app(self):
        try:
            from fastapi import FastAPI, HTTPException, Request, WebSocket
            from fastapi.responses import FileResponse, JSONResponse
            from fastapi.staticfiles import StaticFiles
        except ImportError as exc:
            raise RuntimeError(
                "Web UI dependencies are missing. Install or update ene."
            ) from exc

        @asynccontextmanager
        async def lifespan(_app):
            # Cross-thread wakeups (from worker links or the registry poller)
            # are scheduled onto this loop via call_soon_threadsafe.
            self._loop = asyncio.get_running_loop()
            with self._registry_lock:
                for session in self._sessions.values():
                    session._loop = self._loop
            self._start_poller()
            try:
                yield
            finally:
                # Stop cross-thread wakeups before the loop closes.
                self._poll_stop.set()
                self._loop = None

        app = FastAPI(
            docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
        )
        assets = Path(__file__).with_name("frontend") / "dist"

        @app.middleware("http")
        async def security_headers(request: Request, call_next):
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; connect-src 'self'; "
                "img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
                "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
            )
            return response

        def client_ip(request: Request) -> str:
            return request.client.host if request.client else "unknown"

        def check_rate_limit(ip: str) -> None:
            now = time.time()
            stale = [
                key for key, value in self._login_attempts.items()
                if not value or value[-1] < now - LOGIN_RATE_WINDOW
            ]
            for key in stale:
                self._login_attempts.pop(key, None)
            attempts = self._login_attempts[ip]
            while attempts and attempts[0] < now - LOGIN_RATE_WINDOW:
                attempts.popleft()
            if len(attempts) >= LOGIN_RATE_LIMIT:
                raise HTTPException(status_code=429, detail="Too many login attempts.")
            attempts.append(now)

        def new_login() -> tuple[str, str]:
            login_id = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(24)
            with self._login_lock:
                now = time.time()
                self._logins = {
                    key: value for key, value in self._logins.items()
                    if value[0] >= now
                }
                while len(self._logins) >= MAX_SESSIONS:
                    self._logins.pop(next(iter(self._logins)))
                self._logins[login_id] = (time.time() + SESSION_TTL, csrf)
            return login_id, csrf

        def authenticate_cookie(
            login_id: str | None, *, refresh: bool = True
        ) -> str | None:
            if not login_id:
                return None
            with self._login_lock:
                record = self._logins.get(login_id)
                if record is None:
                    return None
                expires, csrf = record
                if expires < time.time():
                    self._logins.pop(login_id, None)
                    return None
                if refresh:
                    self._logins.pop(login_id)
                    self._logins[login_id] = (time.time() + SESSION_TTL, csrf)
                return csrf

        def valid_origin(origin: str | None, host: str | None) -> bool:
            if not origin or not host:
                return False
            parsed = urlsplit(origin)
            return parsed.scheme in {"http", "https"} and parsed.netloc == host

        @app.get("/")
        async def index():
            return FileResponse(assets / "index.html")

        @app.get("/favicon.svg")
        @app.get("/icon.svg")
        @app.get("/favicon.ico")
        async def favicon():
            return FileResponse(assets / "favicon.svg", media_type="image/svg+xml")

        @app.get("/api/health")
        async def health():
            return {"ok": True}

        @app.post("/api/login")
        async def login(request: Request):
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > MAX_BODY_BYTES:
                        raise HTTPException(status_code=413, detail="Request too large.")
                except ValueError as exc:
                    raise HTTPException(
                        status_code=400, detail="Invalid Content-Length."
                    ) from exc
            ip = client_ip(request)
            check_rate_limit(ip)
            raw_body = await request.body()
            if len(raw_body) > MAX_BODY_BYTES:
                raise HTTPException(status_code=413, detail="Request too large.")
            try:
                body = json.loads(raw_body)
                if not isinstance(body, dict):
                    raise ValueError
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="Invalid JSON.") from exc
            supplied = str(body.get("token", ""))
            if not hmac.compare_digest(
                supplied.encode("utf-8"), self.token.encode("utf-8")
            ):
                raise HTTPException(status_code=401, detail="Invalid token.")
            self._login_attempts.pop(ip, None)
            login_id, csrf = new_login()
            self._log_dim(f"web login from {ip}")
            response = JSONResponse({"ok": True, "csrf": csrf})
            response.set_cookie(
                COOKIE_NAME,
                login_id,
                httponly=True,
                secure=request.url.scheme == "https",
                samesite="strict",
                max_age=SESSION_TTL,
                path="/",
            )
            return response

        @app.post("/api/logout")
        async def logout(request: Request):
            login_id = request.cookies.get(COOKIE_NAME)
            csrf = authenticate_cookie(login_id, refresh=False)
            if csrf is None or not hmac.compare_digest(
                request.headers.get("x-csrf-token", ""), csrf
            ):
                raise HTTPException(status_code=403, detail="Forbidden.")
            with self._login_lock:
                self._logins.pop(login_id, None)
            response = JSONResponse({"ok": True})
            response.delete_cookie(COOKIE_NAME, path="/")
            return response

        def require_login(request: Request) -> str:
            csrf = authenticate_cookie(request.cookies.get(COOKIE_NAME))
            if csrf is None:
                raise HTTPException(status_code=403, detail="Forbidden.")
            return csrf

        def require_csrf(request: Request) -> None:
            """Authenticate a state-changing request.

            Mutations are reachable through a tunnel, so they need the CSRF
            token from the login response in addition to the cookie.
            """
            csrf = authenticate_cookie(
                request.cookies.get(COOKIE_NAME), refresh=False
            )
            if csrf is None or not hmac.compare_digest(
                request.headers.get("x-csrf-token", ""), csrf
            ):
                raise HTTPException(status_code=403, detail="Forbidden.")

        async def read_json_body(request: Request, limit: int) -> dict:
            raw_body = await request.body()
            if len(raw_body) > limit:
                raise HTTPException(status_code=413, detail="Request too large.")
            try:
                body = json.loads(raw_body)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise HTTPException(status_code=400, detail="Invalid JSON.") from exc
            if not isinstance(body, dict):
                raise HTTPException(status_code=400, detail="Invalid JSON.")
            return body

        def resolved_directory(raw: str) -> Path:
            """Resolve a browser-supplied absolute path to an existing directory."""
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute():
                raise HTTPException(
                    status_code=400, detail="Path must be absolute."
                )
            try:
                resolved = candidate.resolve()
                if not resolved.is_dir():
                    raise HTTPException(
                        status_code=400, detail="Not a directory."
                    )
            except OSError as exc:
                raise HTTPException(
                    status_code=400, detail="Path is not reachable."
                ) from exc
            return resolved

        @app.get("/api/sessions")
        async def sessions(request: Request):
            require_login(request)
            return {"sessions": self._session_list()}

        @app.get("/api/fs")
        async def browse(request: Request, path: str = ""):
            """List subdirectories for the workspace picker."""
            require_login(request)
            target = resolved_directory(path) if path else Path.home()
            try:
                entries = sorted(
                    (
                        {
                            "name": entry.name,
                            "path": str(entry),
                            "hidden": entry.name.startswith("."),
                        }
                        for entry in target.iterdir()
                        if entry.is_dir()
                    ),
                    key=lambda entry: entry["name"].lower(),
                )
            except OSError as exc:
                raise HTTPException(
                    status_code=403, detail="Directory is not readable."
                ) from exc
            parent = str(target.parent) if target.parent != target else ""
            return {"path": str(target), "parent": parent, "entries": entries}

        @app.get("/api/workspaces")
        async def workspaces(request: Request):
            """Recently used workspaces, newest first."""
            require_login(request)
            with self._registry_lock:
                records = list(self._records)
            recents: list[str] = []
            for record in sorted(
                records,
                key=lambda item: float(item.get("created_at", 0)),
                reverse=True,
            ):
                workspace = record.get("workspace", "")
                if workspace and workspace not in recents:
                    recents.append(workspace)
            cwd = str(Path.cwd())
            if cwd not in recents:
                recents.append(cwd)
            home = str(Path.home())
            if home not in recents:
                recents.append(home)
            return {"workspaces": recents}

        @app.get("/api/options")
        async def options(request: Request, cwd: str = ""):
            """Model, persona, and reasoning choices for the new-session form."""
            require_login(request)
            from ene.config import conf
            from ene.models import REASONING_EFFORTS
            from ene.personas import list_personas

            models = list(conf.get("openai", {}) or {})
            work_dir = str(resolved_directory(cwd)) if cwd else None
            try:
                personas = sorted(list_personas(work_dir))
            except Exception:
                # A broken project persona must not block session creation.
                personas = []
            return {
                "models": models,
                "default_model": models[0] if models else "",
                "personas": personas,
                "reasoning_efforts": list(REASONING_EFFORTS),
            }

        @app.get("/api/conversations")
        async def conversations(request: Request, cwd: str = ""):
            """Saved conversations in a workspace, for the resume picker."""
            require_login(request)
            from ene.session_store import saved_session_summaries

            work_dir = resolved_directory(cwd) if cwd else Path.cwd()
            live = {
                record.get("conversation_id")
                for record in self._session_list()
                if record.get("cwd") == str(work_dir)
            }
            return {
                "conversations": [
                    {**summary, "live": summary["id"] in live}
                    for summary in saved_session_summaries(work_dir)
                ]
            }

        @app.post("/api/sessions")
        async def create_session(request: Request):
            require_csrf(request)
            body = await read_json_body(request, MAX_CREATE_BODY_BYTES)
            workspace = str(body.get("cwd", "")).strip()
            if not workspace:
                raise HTTPException(
                    status_code=400, detail="A working directory is required."
                )
            options = {
                "workspace": str(resolved_directory(workspace)),
                "name": str(body.get("name", "")).strip(),
                "model": str(body.get("model", "")).strip(),
                "persona": str(body.get("persona", "")).strip(),
                "reasoning_effort": str(body.get("reasoning_effort", "")).strip(),
                "resume": str(body.get("resume", "")).strip(),
            }
            loop = asyncio.get_running_loop()
            try:
                # Starting a worker blocks until it is ready; keep it off the
                # event loop so other browsers stay responsive.
                session = await loop.run_in_executor(
                    None, self.create_session, options
                )
            except LiveBusyError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except LiveError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"session": session.summary()}

        @app.post("/api/sessions/{session_id}/attach")
        async def attach(session_id: str, request: Request):
            require_csrf(request)
            loop = asyncio.get_running_loop()
            try:
                session = await loop.run_in_executor(
                    None, self.attach_session, session_id
                )
            except LiveBusyError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except LiveError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            return {"session": session.summary()}

        @app.post("/api/sessions/{session_id}/detach")
        async def detach(session_id: str, request: Request):
            require_csrf(request)
            loop = asyncio.get_running_loop()
            detached = await loop.run_in_executor(
                None, self.detach_session, session_id
            )
            if not detached:
                raise HTTPException(
                    status_code=404, detail="Session is not attached."
                )
            return {"ok": True}

        # -- browser websocket: control channel or per-session stream -------

        @app.websocket("/api/ws")
        async def websocket_endpoint(websocket: WebSocket):
            await websocket.accept()
            csrf = authenticate_cookie(websocket.cookies.get(COOKIE_NAME))
            if csrf is None or not valid_origin(
                websocket.headers.get("origin"), websocket.headers.get("host")
            ):
                await websocket.close(code=4403)
                return
            with self._client_lock:
                if self._clients >= MAX_CLIENTS:
                    await websocket.close(code=4429)
                    return
                self._clients += 1
                count = self._clients
            session_id = websocket.query_params.get("session", "")
            channel = f"session {session_id[:8]}" if session_id else "control"
            self._log_dim(f"web client connected ({channel}, {count} sockets)")
            try:
                if session_id:
                    await self._serve_browser_session(websocket, csrf, session_id)
                else:
                    await self._serve_browser_control(websocket, csrf)
            except Exception:
                pass
            finally:
                with self._client_lock:
                    self._clients -= 1
                    count = self._clients
                self._log_dim(f"web client disconnected ({channel}, {count} sockets)")

        app.mount("/assets", StaticFiles(directory=assets / "assets"), name="assets")
        return app

    # -- browser: control channel (session list) ---------------------------

    async def _serve_browser_control(self, websocket, csrf: str) -> None:
        await websocket.send_json({
            "type": "sessions",
            "csrf": csrf,
            "sessions": self._session_list(),
        })
        seq = self._control.latest_seq

        async def push_updates():
            nonlocal seq
            while True:
                if self._control.latest_seq != seq:
                    seq = self._control.latest_seq
                    await websocket.send_json({
                        "type": "sessions",
                        "sessions": self._session_list(),
                    })
                    continue
                self._control_notify.clear()
                if self._control.latest_seq != seq:
                    continue
                try:
                    await asyncio.wait_for(
                        self._control_notify.wait(), EVENT_WAIT_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    pass

        async def drain():
            # Control channel accepts only browser heartbeats. A frame that is
            # not JSON is ignored rather than raised: letting it escape would
            # tear down the socket through the endpoint's blanket handler and
            # show up as an unexplained disconnect.
            while True:
                try:
                    payload = await websocket.receive_json()
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict) and payload.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})

        await self._race(push_updates(), drain())

    # -- browser: per-session event stream ---------------------------------

    async def _serve_browser_session(
        self, websocket, csrf: str, session_id: str
    ) -> None:
        session = self.get_session(session_id)
        if session is None:
            await websocket.close(code=4404)
            return

        try:
            raw_seq = websocket.query_params.get("after", "0")
            seq = max(0, int(raw_seq))
        except ValueError:
            seq = 0
        # Clamp an out-of-range cursor (e.g. a reconnect against a fresh stream)
        # so replay always starts from a valid point.
        if seq > session.events.latest_seq:
            seq = 0

        def state_frame(s: RemoteSession, after_seq: int) -> dict:
            return {
                "type": "state",
                "csrf": csrf,
                "session": session_id,
                "stream_id": s.events.stream_id,
                "latest_seq": s.events.latest_seq,
                "operation_id": s.operation_id,
                "process_status": s.process_status,
                "context_status": s.context_status,
                "active_indicator": s.active_indicator,
                "commands": s.commands,
                "prompt": s.prompt,
                "pending": s.pending,
                "oldest_seq": s.events.oldest_seq,
                "replay_truncated": s.events.has_replay_gap(after_seq),
            }

        stream = session.events.stream_id
        await websocket.send_json(state_frame(session, seq))

        async def send_events():
            nonlocal seq, stream
            while True:
                current = self.get_session(session_id)
                if current is None:
                    await websocket.close(code=4404)
                    return
                # The event stream is replaced on agent reconnect (new
                # stream_id, seq reset to 0). Re-issue state and restart from
                # the head so the client rebuilds this session's timeline.
                if current.events.stream_id != stream:
                    stream = current.events.stream_id
                    seq = 0
                    await websocket.send_json(state_frame(current, seq))
                pending = await current.wait_events(seq, EVENT_WAIT_TIMEOUT)
                for event in pending:
                    await websocket.send_json(event.to_dict())
                    seq = event.seq

        async def receive_actions():
            while True:
                try:
                    payload = await websocket.receive_json()
                except WebSocketDisconnect:
                    return
                except json.JSONDecodeError:
                    await websocket.send_json({
                        "type": "rejected", "error": "Invalid JSON message."
                    })
                    continue
                if not isinstance(payload, dict):
                    continue
                action = payload.get("type")
                if action == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue
                if action not in {
                    "submit", "withdraw_pending", "prompt_response", "cancel"
                }:
                    await websocket.send_json({
                        "type": "rejected", "error": "Unknown action type."
                    })
                    continue
                if action == "prompt_response":
                    answer = str(payload.get("answer", ""))
                    if len(answer.encode("utf-8")) > MAX_ANSWER_BYTES:
                        await websocket.send_json({"type": "prompt_ack", "ok": False})
                        continue
                ok = await self._forward_to_worker(session_id, payload)
                if action in {"submit", "withdraw_pending"}:
                    if ok:
                        await websocket.send_json({"type": "accepted"})
                    else:
                        await websocket.send_json({
                            "type": "rejected",
                            "error": "The session is no longer attached.",
                            "action_id": payload.get("action_id", ""),
                        })
                elif action == "prompt_response":
                    await websocket.send_json({"type": "prompt_ack", "ok": ok})
                elif action == "cancel":
                    await websocket.send_json({"type": "cancel_ack", "ok": ok})

        await self._race(send_events(), receive_actions())

    async def _forward_to_worker(self, session_id: str, payload: dict) -> bool:
        """Send one browser action to the attached worker.

        ``submit`` and ``withdraw_pending`` wait for the worker's verdict, so a
        message the agent refuses is reported truthfully instead of being
        acknowledged optimistically. The wait happens in an executor because
        the worker link is a blocking socket.
        """
        session = self.get_session(session_id)
        link = session.link if session is not None else None
        if session is None or link is None:
            return False
        action = payload.get("type")
        loop = asyncio.get_running_loop()
        if action in {"submit", "withdraw_pending"}:
            message = {**payload, "source": "web"}
            try:
                response = await loop.run_in_executor(None, link.request, message)
            except LiveError:
                return False
            if response.get("ok"):
                return True
            error = str(response.get("error", "The session rejected the action."))
            # Publishing into the session stream reaches every browser viewing
            # it, not just the one that acted.
            if action == "submit":
                session.ingest({
                    "type": "submission_rejected",
                    "data": {"action_id": payload.get("action_id", ""), "error": error},
                })
                session.ingest({"type": "warning", "data": {"text": error}})
            else:
                session.ingest({
                    "type": "withdrawal_rejected",
                    "data": {"action_id": payload.get("action_id", ""), "error": error},
                })
            return True
        try:
            await loop.run_in_executor(
                None, link.send, {**payload, "source": "web"}
            )
            return True
        except LiveError:
            return False

    @staticmethod
    async def _race(*coros) -> None:
        tasks = [asyncio.create_task(coro) for coro in coros]
        try:
            done, _ = await asyncio.wait(
                tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    # -- lifecycle ----------------------------------------------------------

    def _write_info(self) -> None:
        HUB_INFO_PATH.parent.mkdir(parents=True, exist_ok=True)
        HUB_INFO_PATH.write_text(
            json.dumps({
                # Discovery is local, so record a concrete connectable address
                # rather than the all-interfaces bind address.
                "host": LOOPBACK_HOST,
                "port": self.port,
                "token": self.token,
                "pid": os.getpid(),
                "started": time.time(),
            }),
            encoding="utf-8",
        )
        try:
            os.chmod(HUB_INFO_PATH, 0o600)
        except OSError:
            pass

    def _remove_info(self) -> None:
        try:
            if HUB_INFO_PATH.exists():
                info = read_hub_info()
                if info and info.get("pid") == os.getpid():
                    HUB_INFO_PATH.unlink()
        except OSError:
            pass

    def _start_poller(self) -> None:
        # A previous run's thread has already exited (lifespan shutdown stops
        # it), so a restarted app needs a fresh one.
        if self._poll_thread is not None and self._poll_thread.is_alive():
            return
        self._poll_stop.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="ene-hub-registry", daemon=True
        )
        self._poll_thread.start()

    def start(self) -> None:
        import uvicorn

        with self._lifecycle_lock:
            if self._thread is not None:
                raise RuntimeError("The hub is already started.")
            config = uvicorn.Config(
                self.app,
                host=self.host,
                port=self.port,
                log_level="warning",
                proxy_headers=True,
                forwarded_allow_ips="127.0.0.1,::1",
                ws_max_size=256 * 1024,
            )
            self._server = uvicorn.Server(config)
            self._thread = threading.Thread(target=self._server.run, daemon=True)
            self._thread.start()
            deadline = time.time() + 10
            while (
                not self._server.started
                and self._thread.is_alive()
                and time.time() < deadline
            ):
                time.sleep(0.05)
            if not self._server.started:
                self._server.should_exit = True
                self._thread.join(timeout=5)
                self._server = None
                self._thread = None
                raise RuntimeError("The hub failed to start.")
            try:
                self._write_info()
            except BaseException:
                self._server.should_exit = True
                self._thread.join(timeout=5)
                self._server = None
                self._thread = None
                raise

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._remove_info()
            self._poll_stop.set()
            # Release every worker slot explicitly. Without this, each attached
            # session would stay reserved until its heartbeat deadline expires,
            # blocking an immediate `ene attach`.
            with self._registry_lock:
                sessions = list(self._sessions.values())
                self._sessions.clear()
            for session in sessions:
                if session.link is not None:
                    try:
                        session.link.detach()
                    except Exception:
                        pass
            if self._poll_thread is not None:
                self._poll_thread.join(timeout=5)
                self._poll_thread = None
            if self._server is not None:
                self._server.should_exit = True
            if self._thread is not None:
                self._thread.join(timeout=5)
            self._server = None
            self._thread = None
