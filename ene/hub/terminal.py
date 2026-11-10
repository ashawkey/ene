"""Interactive shells for the Web UI terminal panel.

The hub runs on the same machine as its workers, so a browser terminal is a
local pseudo-terminal running the user's login shell in the session workspace.
Each :class:`Terminal` is owned by the hub, not by a browser connection:
closing the panel or reloading the page keeps the shell alive, and a new
connection first replays the retained scrollback.

POSIX only; :data:`SUPPORTED` is false elsewhere and creation is refused.
"""

from __future__ import annotations

import os
import secrets
import select
import signal
import struct
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

SUPPORTED = os.name == "posix"
if SUPPORTED:
    import fcntl
    import termios

SCROLLBACK_BYTES = 256 * 1024
MAX_PER_SESSION = 8
MAX_TOTAL = 32
READ_CHUNK = 64 * 1024
POLL_INTERVAL = 0.25            # reader wake-up to notice an exited shell
KILL_GRACE = 1.5                # seconds between SIGHUP and SIGKILL on close
MIN_SIZE, MAX_SIZE = 1, 1000    # accepted terminal rows/columns
DEFAULT_COLS, DEFAULT_ROWS = 80, 24

# A subscriber receives ("data", bytes) and finally ("exit", exit_code).
Subscriber = Callable[[str, object], None]


class TerminalError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def clamp_size(value: object, default: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(MIN_SIZE, min(MAX_SIZE, number))


def _shell() -> str:
    shell = os.environ.get("SHELL", "")
    if shell and os.path.isabs(shell) and os.access(shell, os.X_OK):
        return shell
    for candidate in ("/bin/bash", "/bin/zsh", "/bin/sh"):
        if os.access(candidate, os.X_OK):
            return candidate
    return "/bin/sh"


def _take_controlling_terminal() -> None:  # pragma: no cover - runs in the child
    # start_new_session already made the child a session leader; claim the
    # pty (its stdin) as the controlling terminal so job control works.
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def _set_size(fd: int, cols: int, rows: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


class Terminal:
    """One shell on a pseudo-terminal, with scrollback and live subscribers."""

    def __init__(self, session_id: str, cwd: str, title: str, cols: int, rows: int):
        self.id = secrets.token_urlsafe(9)
        self.session_id = session_id
        self.title = title
        self.cwd = cwd
        self.created = time.time()
        self.exit_code: int | None = None
        self.cols, self.rows = cols, rows
        self._scrollback = bytearray()
        self._subscribers: set[Subscriber] = set()
        self._lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._closing_at: float | None = None
        self._done = threading.Event()

        shell = _shell()
        env = dict(os.environ)
        env.update({"TERM": "xterm-256color", "COLORTERM": "truecolor"})
        env.pop("COLUMNS", None)
        env.pop("LINES", None)
        master, slave = os.openpty()
        try:
            _set_size(master, cols, rows)
            self.process = subprocess.Popen(
                [shell, "-l"],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=cwd,
                env=env,
                start_new_session=True,
                preexec_fn=_take_controlling_terminal,
                close_fds=True,
            )
        except BaseException:
            os.close(master)
            raise
        finally:
            os.close(slave)
        self.master = master
        self.shell = os.path.basename(shell)
        self._reader = threading.Thread(
            target=self._read_loop, name=f"ene-terminal-{self.id}", daemon=True
        )
        self._reader.start()

    # -- state ---------------------------------------------------------------

    @property
    def alive(self) -> bool:
        return not self._done.is_set()

    def describe(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "shell": self.shell,
            "alive": self.alive,
            "exit_code": self.exit_code,
            "created": self.created,
        }

    def subscribe(self, callback: Subscriber) -> tuple[bytes, bool]:
        """Register *callback*; return the scrollback so far and liveness.

        Atomic with the reader, so the subscriber sees every byte exactly once.
        """
        with self._lock:
            if self.alive:
                self._subscribers.add(callback)
            return bytes(self._scrollback), self.alive

    def unsubscribe(self, callback: Subscriber) -> None:
        with self._lock:
            self._subscribers.discard(callback)

    # -- input ---------------------------------------------------------------

    def write(self, data: bytes) -> None:
        if not data or not self.alive:
            return
        with self._write_lock:
            view = memoryview(data)
            while view and self.alive:
                try:
                    written = os.write(self.master, view)
                except InterruptedError:
                    continue
                except OSError:
                    return
                view = view[written:]

    def resize(self, cols: int, rows: int) -> None:
        if not self.alive or (cols, rows) == (self.cols, self.rows):
            return
        self.cols, self.rows = cols, rows
        try:
            _set_size(self.master, cols, rows)  # the kernel signals SIGWINCH
        except OSError:
            pass

    def close(self) -> None:
        """Hang up the shell's process group; the reader escalates to SIGKILL."""
        if not self.alive or self._closing_at is not None:
            return
        self._closing_at = time.monotonic()
        self._signal(signal.SIGHUP)

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)

    # -- reader --------------------------------------------------------------

    def _signal(self, sig: int) -> None:
        try:
            os.killpg(self.process.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def _publish(self, data: bytes) -> None:
        with self._lock:
            self._scrollback += data
            excess = len(self._scrollback) - SCROLLBACK_BYTES
            if excess > 0:
                # Cut at a line boundary so replay does not start mid-sequence.
                newline = self._scrollback.find(b"\n", excess)
                cut = newline + 1 if 0 <= newline < excess + 4096 else excess
                del self._scrollback[:cut]
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback("data", data)
            except Exception:
                self.unsubscribe(callback)

    def _read_loop(self) -> None:
        try:
            while True:
                if (
                    self._closing_at is not None
                    and time.monotonic() - self._closing_at > KILL_GRACE
                ):
                    # Leftovers that ignored the hangup keep the pty busy.
                    self._signal(signal.SIGKILL)
                    if self.process.poll() is not None:
                        break
                try:
                    ready, _, _ = select.select([self.master], [], [], POLL_INTERVAL)
                except InterruptedError:
                    continue
                except (OSError, ValueError):
                    break
                if not ready:
                    # The shell exited but a leftover child may still hold the
                    # pty open; stop once nothing more is arriving.
                    if self.process.poll() is not None:
                        break
                    continue
                try:
                    data = os.read(self.master, READ_CHUNK)
                except InterruptedError:
                    continue
                except OSError:  # EIO: every slave fd is closed
                    break
                if not data:
                    break
                self._publish(data)
        finally:
            self._finish()

    def _finish(self) -> None:
        try:
            code = self.process.wait(timeout=KILL_GRACE)
        except subprocess.TimeoutExpired:
            self._signal(signal.SIGKILL)
            code = self.process.wait()
        try:
            os.close(self.master)
        except OSError:
            pass
        with self._lock:
            self.exit_code = code
            self._done.set()
            subscribers = list(self._subscribers)
            self._subscribers.clear()
        for callback in subscribers:
            try:
                callback("exit", code)
            except Exception:
                pass


class TerminalManager:
    """Every hub terminal, grouped by the live session that opened it."""

    def __init__(self):
        self._terminals: dict[str, Terminal] = {}
        self._counters: dict[str, int] = {}
        self._lock = threading.Lock()

    def create(self, session_id: str, cwd: str, cols: object = None, rows: object = None) -> Terminal:
        if not SUPPORTED:
            raise TerminalError("Terminals are not supported on this platform.", 501)
        if not Path(cwd).is_dir():
            raise TerminalError("The session workspace is not a directory.", 409)
        with self._lock:
            if len(self._terminals) >= MAX_TOTAL:
                raise TerminalError("Too many open terminals.", 429)
            if sum(t.session_id == session_id for t in self._terminals.values()) >= MAX_PER_SESSION:
                raise TerminalError("Too many terminals for this session.", 429)
            number = self._counters.get(session_id, 0) + 1
            self._counters[session_id] = number
            try:
                terminal = Terminal(
                    session_id,
                    cwd,
                    f"Terminal {number}",
                    clamp_size(cols, DEFAULT_COLS),
                    clamp_size(rows, DEFAULT_ROWS),
                )
            except OSError as exc:
                raise TerminalError(f"Could not start a shell: {exc}", 500) from exc
            self._terminals[terminal.id] = terminal
        return terminal

    def get(self, session_id: str, terminal_id: str) -> Terminal | None:
        with self._lock:
            terminal = self._terminals.get(terminal_id)
        if terminal is None or terminal.session_id != session_id:
            return None
        return terminal

    def list(self, session_id: str) -> list[dict]:
        with self._lock:
            terminals = [t for t in self._terminals.values() if t.session_id == session_id]
        terminals.sort(key=lambda t: t.created)
        return [t.describe() for t in terminals]

    def remove(self, session_id: str, terminal_id: str) -> bool:
        """Close a terminal (if running) and forget it."""
        with self._lock:
            terminal = self._terminals.get(terminal_id)
            if terminal is None or terminal.session_id != session_id:
                return False
            del self._terminals[terminal_id]
        terminal.close()
        return True

    def close_session(self, session_id: str) -> None:
        with self._lock:
            terminals = [t for t in self._terminals.values() if t.session_id == session_id]
            for terminal in terminals:
                del self._terminals[terminal.id]
            self._counters.pop(session_id, None)
        for terminal in terminals:
            terminal.close()

    def close_all(self, timeout: float = 0.0) -> None:
        with self._lock:
            terminals = list(self._terminals.values())
            self._terminals.clear()
            self._counters.clear()
        for terminal in terminals:
            terminal.close()
        deadline = time.monotonic() + timeout
        for terminal in terminals:
            terminal.wait(max(0.0, deadline - time.monotonic()))
