"""Hub-side attachment to a live worker.

The hub is a client of the persistent worker, exactly like a terminal: it takes
the worker's single attachment slot over the framed loopback protocol in
:mod:`ene.live`, streams the worker's events, and sends browser actions back.
Ownership is therefore mutually exclusive with a terminal attachment, and the
worker's existing heartbeat/idle handling releases a hub that dies abruptly.

This module deliberately contains no rendering: events are handed to a callback
(the hub's :class:`~ene.hub.RemoteSession` ingest) which re-publishes them to
browsers.
"""

from __future__ import annotations

import json
import socket
import threading
import uuid
from typing import Any, Callable

from ene.live import (
    REQUEST_TIMEOUT,
    TERMINAL_PING_INTERVAL,
    LiveError,
    connect,
    recv_frame,
    send_frame,
)


class WorkerLink:
    """One attached worker connection owned by the hub."""

    def __init__(
        self,
        record: dict[str, Any],
        *,
        on_event: Callable[[dict[str, Any]], None],
        on_closed: Callable[[], None],
    ):
        self.record = record
        self.runtime_id = str(record.get("runtime_id", ""))
        self.on_event = on_event
        self.on_closed = on_closed
        self.sock: socket.socket | None = None
        self.stopped = threading.Event()
        self.detaching = threading.Event()
        self._send_lock = threading.Lock()
        self._action_lock = threading.Lock()
        self._action_waiters: dict[str, tuple[threading.Event, dict[str, Any]]] = {}
        self._reader: threading.Thread | None = None
        self._pinger: threading.Thread | None = None

    # -- lifecycle ----------------------------------------------------------

    def attach(self) -> dict[str, Any]:
        """Take the worker's attachment slot and return its status payload.

        Raises :class:`~ene.live.LiveBusyError` when a terminal already owns
        the session, and :class:`~ene.live.LiveError` for transport failures.
        """
        sock = connect(self.record, "attach", client="web")
        try:
            attached = recv_frame(sock)
            # Replay and live events arrive without a deadline; the ping thread
            # is what proves this attachment is still alive to the worker.
            sock.settimeout(None)
        except (OSError, EOFError, ValueError, json.JSONDecodeError, LiveError) as exc:
            sock.close()
            raise LiveError("Could not attach to the live session") from exc
        self.sock = sock
        session = attached.get("session", {})
        self._reader = threading.Thread(
            target=self._read_loop, name=f"ene-hub-link-{self.runtime_id[:8]}",
            daemon=True,
        )
        self._pinger = threading.Thread(
            target=self._ping_loop, name=f"ene-hub-ping-{self.runtime_id[:8]}",
            daemon=True,
        )
        self._reader.start()
        self._pinger.start()
        return session if isinstance(session, dict) else {}

    def detach(self) -> None:
        """Release the slot and wait for the worker to close the connection."""
        if self.stopped.is_set():
            self._close()
            return
        self.detaching.set()
        try:
            self.send({"type": "detach"})
        except LiveError:
            self.stopped.set()
        # The worker clears its attachment before closing the socket. Waiting
        # for the reader to observe that close keeps an immediate reattach
        # (terminal or web) from racing this release.
        self.stopped.wait(REQUEST_TIMEOUT)
        self._close()

    def _close(self) -> None:
        self.stopped.set()
        sock = self.sock
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        for thread in (self._reader, self._pinger):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=1)

    # -- sending ------------------------------------------------------------

    def send(self, message: dict[str, Any]) -> None:
        sock = self.sock
        if sock is None or self.stopped.is_set():
            raise LiveError("Not attached")
        try:
            with self._send_lock:
                send_frame(sock, message)
        except LiveError:
            raise
        except OSError as exc:
            raise LiveError("Connection to the live session was lost") from exc

    def request(self, message: dict[str, Any]) -> dict[str, Any]:
        """Send an action and wait for the worker to accept or reject it."""
        request_id = uuid.uuid4().hex
        done = threading.Event()
        response: dict[str, Any] = {}
        with self._action_lock:
            self._action_waiters[request_id] = (done, response)
        try:
            self.send({**message, "request_id": request_id})
            if not done.wait(REQUEST_TIMEOUT):
                raise LiveError("Live-session action timed out")
            return response
        finally:
            with self._action_lock:
                self._action_waiters.pop(request_id, None)

    # -- receiving ----------------------------------------------------------

    def _read_loop(self) -> None:
        assert self.sock is not None
        try:
            while not self.stopped.is_set():
                message = recv_frame(self.sock)
                kind = message.get("type")
                if kind == "event":
                    event = message.get("event")
                    if isinstance(event, dict):
                        try:
                            self.on_event(event)
                        except Exception:
                            # A rendering/publishing fault must not tear down
                            # the attachment.
                            continue
                elif kind == "action_result":
                    request_id = str(message.get("request_id", ""))
                    with self._action_lock:
                        waiter = self._action_waiters.get(request_id)
                    if waiter is not None:
                        done, response = waiter
                        response.update(message)
                        done.set()
        except (OSError, EOFError, ValueError, json.JSONDecodeError, LiveError):
            pass
        finally:
            self.stopped.set()
            with self._action_lock:
                waiters = list(self._action_waiters.values())
            for done, response in waiters:
                response.update(ok=False, error="Connection to the session was lost")
                done.set()
            try:
                self.on_closed()
            except Exception:
                pass

    def _ping_loop(self) -> None:
        """Heartbeat so an abruptly killed hub cannot reserve the slot forever."""
        try:
            while not self.stopped.is_set():
                self.send({"type": "ping"})
                self.stopped.wait(TERMINAL_PING_INTERVAL)
        except LiveError:
            # The reader loop owns disconnect reporting.
            pass
