"""Interactive shells for the Web UI terminal panel.

The hub runs on the same machine as its workers, so a browser terminal is a
local pseudo-terminal running the user's login shell in the session workspace.
Each :class:`Terminal` is owned by the hub, not by a browser connection:
closing the panel or reloading the page keeps the shell alive, and a new
connection first replays the retained scrollback, preceded by the terminal
modes (alternate screen, cursor keys, mouse, bracketed paste) that were in
effect where the retained window starts.

POSIX only; :data:`SUPPORTED` is false elsewhere and creation is refused.
"""

from __future__ import annotations

import os
import re
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
# Input a program has not read yet (a busy full-screen app, a hung ssh) waits
# here once the pty's own buffer is full; beyond this it is dropped.
MAX_PENDING_INPUT = 1024 * 1024

# DEC private modes whose state is carried past the scrollback cut: without
# them a replay that no longer holds the switching sequence would draw a
# full-screen program on the main screen, or send it the wrong key codes.
# Value: the mode's power-on state.
TRACKED_MODES = {
    1: False,     # application cursor keys
    25: True,     # cursor visible
    47: False,    # alternate screen (legacy)
    1000: False,  # mouse: clicks
    1002: False,  # mouse: drags
    1003: False,  # mouse: all motion
    1004: False,  # focus events
    1006: False,  # mouse: SGR encoding
    1047: False,  # alternate screen
    1049: False,  # alternate screen, saving the cursor
    2004: False,  # bracketed paste
}
_MODE_SEQUENCE = re.compile(rb"\x1b\[\?([0-9;]+)([hl])")

# Variables describing the terminal the hub was started from. Inherited, they
# would make tmux refuse to nest, or programs that probe the terminal (Claude
# Code, Codex, editors) enable features xterm.js lacks.
INHERITED_TERMINAL_PREFIXES = (
    "TERM_",            # TERM_PROGRAM, TERM_PROGRAM_VERSION, TERM_SESSION_ID
    "TMUX",             # TMUX, TMUX_PANE
    "ITERM_",
    "KITTY_",
    "WEZTERM_",
    "GHOSTTY_",
    "ALACRITTY_",
    "KONSOLE_",
    "GNOME_TERMINAL_",
    "VTE_",
    "WT_",              # Windows Terminal
)
INHERITED_TERMINAL_NAMES = {
    "STY",              # GNU screen
    "WINDOWID",
    "COLORFGBG",
    "LC_TERMINAL",
    "LC_TERMINAL_VERSION",
    "TERMINAL_EMULATOR",
    "CLAUDECODE",       # set inside Claude Code; `claude` refuses to nest
    "CLAUDE_CODE_ENTRYPOINT",
    "CODEX_SANDBOX",
    "CODEX_SANDBOX_NETWORK_DISABLED",
}
ALTERNATE_SCREEN_MODES = (47, 1047, 1049)

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


def shell_environment(base: dict[str, str]) -> dict[str, str]:
    """The environment for a browser shell: *base* without the hub's own
    terminal identity, describing xterm.js instead."""
    env = {
        key: value
        for key, value in base.items()
        if key not in INHERITED_TERMINAL_NAMES
        and not key.startswith(INHERITED_TERMINAL_PREFIXES)
        and key not in ("COLUMNS", "LINES")
    }
    env.update({"TERM": "xterm-256color", "COLORTERM": "truecolor"})
    return env


def apply_modes(modes: dict[int, bool], data: bytes | bytearray) -> None:
    """Update *modes* with the tracked mode switches found in *data*."""
    for match in _MODE_SEQUENCE.finditer(data):
        enable = match.group(2) == b"h"
        for part in match.group(1).split(b";"):
            if part.isdigit() and int(part) in TRACKED_MODES:
                modes[int(part)] = enable


def mode_prefix(modes: dict[int, bool]) -> bytes:
    """Escape sequences that restore *modes* on a freshly reset terminal."""
    return b"".join(
        b"\x1b[?%d%s" % (mode, b"h" if on else b"l")
        for mode, on in sorted(modes.items())
        if on != TRACKED_MODES[mode]
    )


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
        # Tracked modes in effect where the retained scrollback starts.
        self._base_modes: dict[int, bool] = {}
        self._subscribers: set[Subscriber] = set()
        self._lock = threading.Lock()
        # Guards the master fd for writers: input, resizes, and the final close.
        self._write_lock = threading.Lock()
        self._pending = bytearray()
        self._fd_closed = False
        self._closing_at: float | None = None
        self._done = threading.Event()

        shell = _shell()
        env = shell_environment(dict(os.environ))
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
        # Writes must never block a hub thread (see write); reads are
        # select-driven, so this only changes how a full input buffer behaves.
        os.set_blocking(master, False)
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
        """Register *callback*; return the replay so far and liveness.

        The replay is the retained scrollback, prefixed with the modes that
        were in effect where it starts. Atomic with the reader, so the
        subscriber sees every byte exactly once.
        """
        with self._lock:
            if self.alive:
                self._subscribers.add(callback)
            return mode_prefix(self._base_modes) + bytes(self._scrollback), self.alive

    def alternate_screen(self) -> bool:
        """True while the output so far leaves the alternate screen active."""
        with self._lock:
            modes = dict(self._base_modes)
            apply_modes(modes, self._scrollback)
        return any(modes.get(mode) for mode in ALTERNATE_SCREEN_MODES)

    def unsubscribe(self, callback: Subscriber) -> None:
        with self._lock:
            self._subscribers.discard(callback)

    # -- input ---------------------------------------------------------------

    def write(self, data: bytes) -> None:
        """Send input to the shell without ever blocking.

        A program that stops reading fills the pty's input buffer; a blocking
        write would then park the calling thread, even past ``close``. What
        does not fit waits in a bounded queue that the reader thread flushes
        once the program reads again.
        """
        if not data or not self.alive:
            return
        with self._write_lock:
            if self._fd_closed:
                return
            if not self._pending:
                data = data[self._write_now(data):]
            room = MAX_PENDING_INPUT - len(self._pending)
            if data and room > 0:
                self._pending += data[:room]

    def _write_now(self, data: bytes | bytearray) -> int:
        """Write what the pty accepts now; hold ``_write_lock``. Returns the
        bytes consumed (all of them once the shell is gone)."""
        written = 0
        # Released on exit: the pending bytearray is resized right after.
        with memoryview(data) as view:
            while written < len(view):
                try:
                    written += os.write(self.master, view[written:])
                except InterruptedError:
                    continue
                except BlockingIOError:
                    break
                except OSError:  # the shell is gone: drop the input
                    return len(view)
        return written

    def _flush_input(self) -> None:
        with self._write_lock:
            if self._pending and not self._fd_closed:
                del self._pending[:self._write_now(self._pending)]

    def foreground_busy(self) -> bool:
        """True while a program other than the shell owns the terminal.

        Browsers use it to decide how to narrow the screen: a shell prompt
        redraws correctly over re-wrapped lines, but a program that repaints
        its own region (a prompt with a toolbar, a TUI) would leave copies.
        """
        with self._write_lock:
            if self._fd_closed:
                return False
            try:
                return os.tcgetpgrp(self.master) != self.process.pid
            except OSError:
                return False

    def resize(self, cols: int, rows: int) -> None:
        if not self.alive or (cols, rows) == (self.cols, self.rows):
            return
        self.cols, self.rows = cols, rows
        with self._write_lock:
            if self._fd_closed:
                return
            try:
                _set_size(self.master, cols, rows)  # the kernel signals SIGWINCH
            except OSError:
                pass

    def nudge(self, shifted: bool) -> None:
        """Shift the pty a row off its size, or (``shifted=False``) restore it.

        A full-screen program repaints itself on the resulting SIGWINCH, which
        repairs what a partial scrollback replay could not draw. The recorded
        size never changes, so a real resize in between is never undone.
        """
        if not self.alive:
            return
        rows = self.rows
        if shifted:
            rows = rows - 1 if rows > MIN_SIZE else rows + 1
        with self._write_lock:
            if self._fd_closed:
                return
            try:
                _set_size(self.master, self.cols, rows)
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
                apply_modes(self._base_modes, self._scrollback[:cut])
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
                # Wait for output, and for room to flush queued input.
                waiting_input = [self.master] if self._pending else []
                try:
                    ready, writable, _ = select.select(
                        [self.master], waiting_input, [], POLL_INTERVAL
                    )
                except InterruptedError:
                    continue
                except (OSError, ValueError):
                    break
                if writable:
                    self._flush_input()
                if not ready:
                    # The shell exited but a leftover child may still hold the
                    # pty open; stop once nothing more is arriving.
                    if self.process.poll() is not None:
                        break
                    continue
                try:
                    data = os.read(self.master, READ_CHUNK)
                except (InterruptedError, BlockingIOError):
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
        # Under the write lock, so no writer can reach a reused fd number.
        with self._write_lock:
            self._fd_closed = True
            self._pending.clear()
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
