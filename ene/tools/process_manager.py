"""Core managed-process tools, registry, status, and lifecycle."""

from __future__ import annotations

import json
import math
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .constants import (
    MAX_PROCESS_LOG_BYTES,
    MAX_PROCESS_LOG_TAIL_CHARS,
    MAX_TOOL_OUTPUT_CHARS,
)
from ene.utils.process import (
    agent_process_env,
    decode_output_text,
    windows_hidden_process_kwargs,
    windows_utf8_powershell_command,
)

from .process_util import (
    _close_windows_job,
    _create_windows_job,
    _resume_windows_process,
    _terminate_process,
    _windows_job_active_processes,
)


# Status markers: a watcher can still notify the agent; a background process
# runs without ever notifying it.
WATCHER_GLYPH = "↻"
BACKGROUND_GLYPH = "⚙"


def format_background_counts(watchers: int, background: int) -> str:
    """Compact marker such as ``↻2 ⚙1``; empty when nothing is running."""
    parts = []
    if watchers:
        parts.append(f"{WATCHER_GLYPH}{watchers}")
    if background:
        parts.append(f"{BACKGROUND_GLYPH}{background}")
    return " ".join(parts)


def _format_process_elapsed(seconds: float) -> str:
    """Format process runtime like the terminal status timer."""
    total = max(0, math.ceil(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def process_status_snapshot(
    running: int,
    finished: int,
    activity: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return structured process activity suitable for browser clients."""
    processes = []
    for item in activity or []:
        if item["status"] != "running":
            continue
        label = item.get("label") or " ".join(item["command"].split())
        processes.append({
            "process_id": str(item["process_id"]),
            "label": _clean_process_line(label)[:_PROCESS_LABEL_MAX_CHARS],
            "elapsed_seconds": max(0, float(item["elapsed_seconds"])),
            "last_line": _clean_process_line(item.get("last_line", "")),
            "watching": bool(item.get("watching")),
        })
    return {"running": running, "finished": finished, "processes": processes}


def format_process_status(
    running: int,
    finished: int,
    activity: list[dict[str, Any]] | None = None,
) -> str:
    """Return the multiline process summary used by the terminal UI."""
    if running <= 0:
        return ""
    active = process_status_snapshot(running, finished, activity)["processes"]
    watchers = sum(item["watching"] for item in active)
    counts = format_background_counts(watchers, len(active) - watchers)
    lines = [f"{running} running" + (f" ({counts})" if counts else "") + f" · {finished} finished"]
    for index, item in enumerate(active):
        marker = "└" if index == len(active) - 1 else "├"
        glyph = WATCHER_GLYPH if item["watching"] else BACKGROUND_GLYPH
        elapsed = _format_process_elapsed(item["elapsed_seconds"])
        lines.append(
            f"{marker} {glyph} {item['process_id']} [{item['label']}] ({elapsed}) "
            f"{item['last_line']}".rstrip()
        )
    return "\n".join(lines)


_PROCESS_LABEL_MAX_CHARS = 40
_PROCESS_LINE_MAX_CHARS = 80
# Log bytes still reach the file and output notifications immediately; only
# live status refreshes are coalesced.
_ACTIVITY_NOTIFY_INTERVAL = 0.5
_LAST_LINE_MAX_CHARS = 80
NOTIFY_MODES = ("exit", "output", "jsonl", "none")
# Output notifications carry one line each; the inbox bounds the backlog.
_NOTIFY_LINE_MAX_BYTES = 8192
_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


def _clean_process_line(text: str) -> str:
    """Return one bounded printable line suitable for status rendering."""
    text = _ANSI_RE.sub("", text)
    text = "".join(char for char in text if char.isprintable())
    return " ".join(text.split())[:_PROCESS_LINE_MAX_CHARS]


class ProcessManagerMixin:
    """Manage session-scoped background processes and their UI observers."""

    def _init_process_registry(self) -> None:
        self._processes: dict[str, dict[str, Any]] = {}
        self._process_lock = threading.Lock()
        self._next_process_number = 1
        self._process_condition = threading.Condition(self._process_lock)
        self._process_notify_lock = threading.RLock()
        self._process_listeners: set[Callable[[int, int], None]] = set()
        self._last_process_counts = (0, 0)
        self._process_status_callback = None
        self._activity_flush_timer: threading.Timer | None = None
        # Called whenever background_counts() may have changed.
        self.background_callback: Callable[[], None] | None = None

    def add_process_listener(
        self, listener: Callable[[int, int], None], *, notify: bool = True
    ) -> None:
        # Serialize the initial snapshot with lifecycle notifications too; an
        # observer installed during a completion must not receive stale counts
        # after the completion update.
        with self._process_notify_lock:
            with self._process_lock:
                self._process_listeners.add(listener)
            if notify:
                listener(*self.process_counts())

    def set_process_status_callback(
        self, callback: Callable[[int, int], None] | None
    ) -> None:
        """Install the session-level observer used by terminal and web UIs."""
        previous = getattr(self, "_process_status_callback", None)
        if previous is not None:
            self.remove_process_listener(previous)
        self._process_status_callback = callback
        if callback is not None:
            self.add_process_listener(callback)

    def remove_process_listener(self, listener: Callable[[int, int], None]) -> None:
        with self._process_lock:
            self._process_listeners.discard(listener)

    def process_counts(self) -> tuple[int, int]:
        """Return ``(running, finished)`` from authoritative process records."""
        with self._process_lock:
            records = list(self._processes.values())
        running = sum(self._process_info(record)["status"] == "running" for record in records)
        return running, len(records) - running

    def process_activity(self) -> list[dict[str, Any]]:
        """Return status-bar and ``/ps`` metadata for every managed process."""
        with self._process_lock:
            records = list(self._processes.values())
            snapshots = [
                (
                    self._process_info(record),
                    record["started_at"],
                    record.get("last_line") or "",
                    self._is_watcher(record),
                )
                for record in records
            ]
        return [
            {
                **info,
                "elapsed_seconds": time.time() - started_at,
                "last_line": _clean_process_line(last_line),
                "watching": watching,
            }
            for info, started_at, last_line, watching in snapshots
        ]

    def _notify_background_changed(self) -> None:
        callback = self.background_callback
        if callback is None:
            return
        try:
            callback()
        except Exception:
            # Like process listeners, a status observer must not break work.
            pass

    def _notify_process_status(self, *, force: bool = False) -> None:
        # Lifecycle changes can arrive from several capture threads. Serialize
        # both the count snapshot and callback delivery so an older update can
        # never overtake a newer one in the terminal or Web UI.
        with self._process_notify_lock:
            self._notify_background_changed()
            counts = self.process_counts()
            with self._process_lock:
                if not force and counts == self._last_process_counts:
                    return
                self._last_process_counts = counts
                listeners = list(self._process_listeners)
            for listener in listeners:
                try:
                    listener(*counts)
                except Exception:
                    # Process lifecycle must not depend on a UI observer.
                    pass

    def _schedule_activity_flush(self) -> None:
        """Coalesce process log activity into one status notify per interval."""
        with self._process_notify_lock:
            if self._activity_flush_timer is not None:
                return
            timer = threading.Timer(_ACTIVITY_NOTIFY_INTERVAL, self._flush_activity)
            timer.daemon = True
            self._activity_flush_timer = timer
            timer.start()

    def _flush_activity(self) -> None:
        """Publish the current process activity snapshot (timer callback)."""
        with self._process_notify_lock:
            self._activity_flush_timer = None
        self._notify_process_status(force=True)
        if self.process_counts()[0]:
            self._schedule_activity_flush()

    @staticmethod
    def _release_completed_windows_job(record: dict[str, Any]) -> bool:
        """Close a completed Windows job exactly once."""
        with record["job_lock"]:
            job_handle = record["job_handle"]
            if job_handle is None or _windows_job_active_processes(job_handle):
                return False
            record["job_handle"] = None
        _close_windows_job(job_handle)
        return True

    def _process_info(self, record: dict[str, Any]) -> dict[str, Any]:
        with record["state_lock"]:
            proc = record["process"]
            exit_code = record["exit_code"]
            if proc is not None:
                polled = proc.poll()
                if polled is not None:
                    record["exit_code"] = exit_code = polled
        if record["job_handle"] is not None:
            self._release_completed_windows_job(record)
            if record["job_handle"] is not None:
                exit_code = None
        return {
            "process_id": record["process_id"],
            "pid": record["pid"],
            "status": "running" if exit_code is None else "exited",
            "exit_code": exit_code,
            "command": record["command"],
            "label": record["label"],
            "cwd": record["cwd"],
            "notify": record["notify"],
            "log_path": record["log_path"],
            "log_truncated": record["log_truncated"],
            "log_error": record.get("log_error"),
            "capture_error": record.get("capture_error"),
        }

    def _find_process_record_locked(self, process_id: str) -> dict[str, Any] | None:
        """Resolve a session-local managed process ID with the registry locked."""
        return self._processes.get(process_id)

    def _start_process(
        self,
        command: str,
        cwd: str | None = None,
        label: str | None = None,
        notify: str = "exit",
        pattern: str | None = None,
    ) -> dict[str, Any]:
        """Start a session-managed background process with file-backed output.

        Unless *notify* is ``"none"``, exit (other than through ``stop_process``)
        posts an inbox notification; ``"output"`` and ``"jsonl"`` also post
        selected output lines as they arrive.
        """
        if label is not None:
            if not isinstance(label, str):
                return {"error": "label must be a string", "success": False}
            label = " ".join(label.split())
            if not label:
                return {"error": "label must not be empty", "success": False}
        if notify not in NOTIFY_MODES:
            return {
                "error": f"notify must be one of: {', '.join(NOTIFY_MODES)}",
                "success": False,
            }
        line_filter = None
        if pattern is not None:
            if notify != "output":
                return {"error": "pattern requires notify=output", "success": False}
            if not isinstance(pattern, str) or not pattern:
                return {"error": "pattern must be a non-empty string", "success": False}
            try:
                line_filter = re.compile(pattern)
            except re.error as exc:
                return {"error": f"Invalid pattern: {exc}", "success": False}
        sink = self.inbox_sink if notify != "none" else None
        if sink is None:
            # A bare executor has no agent to wake; report the effective mode.
            notify = "none"
        cwd = str(self._resolve_path(cwd or "."))
        with self._process_lock:
            process_id = str(self._next_process_number)
            self._next_process_number += 1
        log_dir = self._resolve_path(".ene/processes")
        log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        log_dir.chmod(0o700)
        # IDs are intentionally session-local and easy to type. Keep a random
        # log suffix so concurrent sessions in one workspace cannot collide.
        log_path = log_dir / f"{process_id}-{uuid.uuid4().hex[:8]}.log"
        log_file = log_path.open("xb", buffering=0)
        try:
            log_path.chmod(0o600)
            if sys.platform == "win32":
                shell_cmd = [
                    "powershell", "-NoLogo", "-Command",
                    windows_utf8_powershell_command(command),
                ]
                proc = subprocess.Popen(
                    shell_cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=cwd,
                    env=agent_process_env(self.model_alias, self.reasoning_effort),
                    **windows_hidden_process_kwargs(
                        subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000004
                    ),
                )
                job_handle = None
                try:
                    job_handle = _create_windows_job(proc)
                    _resume_windows_process(proc)
                except Exception:
                    if job_handle is not None:
                        _close_windows_job(job_handle, terminate=True)
                    else:
                        _terminate_process(proc)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                    raise
                process_backend = "windows_job"
            elif sys.platform.startswith("linux"):
                job_handle = None
                supervisor = Path(__file__).parent / "process_supervisor.py"
                shell_cmd = [
                    sys.executable, str(supervisor), str(os.getpid()), command
                ]
                proc = subprocess.Popen(
                    shell_cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=cwd,
                    start_new_session=True,
                    env=agent_process_env(self.model_alias, self.reasoning_effort),
                )
                process_backend = "linux_supervisor"
            else:
                job_handle = None
                shell_cmd = ["/bin/bash", "-lc", command]
                proc = subprocess.Popen(
                    shell_cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    cwd=cwd,
                    start_new_session=True,
                    env=agent_process_env(self.model_alias, self.reasoning_effort),
                )
                process_backend = "process_group"
        except Exception:
            log_file.close()
            log_path.unlink(missing_ok=True)
            raise

        record = {
            "process_id": process_id,
            "pid": proc.pid,
            "process": proc,
            "exit_code": None,
            "command": command,
            "label": label,
            "cwd": cwd,
            "notify": notify,
            "stop_requested": False,
            # Held while posting a notification and while requesting a stop,
            # so no notification is posted once a stop has been requested.
            "notify_lock": threading.Lock(),
            "exit_handled": False,
            "log_path": str(log_path.relative_to(self._resolve_path("."))),
            "started_at": time.time(),
            "log_truncated": False,
            "log_bytes": 0,
            "last_line": "",
            "job_handle": job_handle,
            "process_backend": process_backend,
            "job_lock": threading.Lock(),
            "state_lock": threading.Lock(),
        }

        source = f"process:{process_id}" + (f" {label}" if label else "")

        def post(text: str) -> None:
            # A deliberate stop ends every notification, including output
            # still draining from the pipe after the stop.
            with record["notify_lock"]:
                if not record["stop_requested"]:
                    sink(text, source)

        def notify_line(raw: bytes) -> None:
            # Carriage returns redraw a terminal line (progress bars); only
            # the final redraw is what the line shows.
            raw = raw.rstrip(b"\r").rsplit(b"\r", 1)[-1]
            text = _ANSI_RE.sub("", decode_output_text(raw[:_NOTIFY_LINE_MAX_BYTES]))
            if not text.strip():
                return
            if notify == "jsonl":
                try:
                    if not isinstance(json.loads(text), dict):
                        return
                # Deep nesting raises RecursionError, not a decode error.
                except (ValueError, RecursionError):
                    return
            elif line_filter is not None and not line_filter.search(text):
                return
            post(text)

        def notify_exit() -> None:
            info = self._process_info(record)
            message = (
                f"Process {process_id}"
                + (f" [{label}]" if label else "")
                + f" exited with code {info['exit_code']}. Log: {info['log_path']}"
            )
            last_line = _clean_process_line(record.get("last_line") or "")
            if last_line:
                message += f"\nLast output line: {last_line}"
            post(message)

        def post_notification(post: Callable[..., None], *args: Any) -> None:
            # A failed notification must not stop log capture, skip the exit
            # notification, or leave the process looking pending forever.
            try:
                post(*args)
            except Exception:
                pass

        def capture_output() -> None:
            written = 0
            log_enabled = True
            tail = b""
            partial = b""
            try:
                while chunk := proc.stdout.read1(65536):
                    if notify in ("output", "jsonl"):
                        *lines, partial = (partial + chunk).split(b"\n")
                        # Keep only the latest redraw of a line still being
                        # rewritten with carriage returns.
                        redraw = partial.rstrip(b"\r").rfind(b"\r")
                        if redraw >= 0:
                            partial = partial[redraw + 1:]
                        # An unterminated line still notifies once it is long.
                        if len(partial) > _NOTIFY_LINE_MAX_BYTES:
                            lines.append(partial)
                            partial = b""
                        for line in lines:
                            post_notification(notify_line, line)
                    remaining = MAX_PROCESS_LOG_BYTES - written
                    if remaining > 0 and log_enabled:
                        data = chunk[:remaining]
                        try:
                            log_file.write(data)
                        except OSError as exc:
                            record["log_error"] = str(exc)
                            record["log_truncated"] = True
                            log_enabled = False
                        else:
                            written += len(data)
                            with self._process_condition:
                                record["log_bytes"] = written
                                tail = (tail + data)[-8192:]
                                lines = [line for line in tail.split(b"\n") if line.strip()]
                                if lines:
                                    record["last_line"] = (
                                        decode_output_text(lines[-1].strip())
                                        [:_LAST_LINE_MAX_CHARS]
                                    )
                                self._process_condition.notify_all()
                    if len(chunk) > remaining:
                        record["log_truncated"] = True
                    # Refresh the live status on a coalescing timer; the flush
                    # reads current state, so it publishes the newest line.
                    self._schedule_activity_flush()
            except OSError as exc:
                record["capture_error"] = str(exc)
            finally:
                try:
                    proc.stdout.close()
                    log_file.close()
                    proc.wait()
                    with record["state_lock"]:
                        record["exit_code"] = proc.returncode
                    if record["process_backend"] == "windows_job":
                        while record["job_handle"] is not None:
                            self._release_completed_windows_job(record)
                            if record["job_handle"] is not None:
                                time.sleep(0.1)
                    with record["state_lock"]:
                        record["process"] = None
                    with self._process_condition:
                        self._process_condition.notify_all()
                    self._notify_process_status()
                finally:
                    if sink is not None:
                        if partial:
                            post_notification(notify_line, partial)
                        post_notification(notify_exit)
                    # Waiters treat the process as pending until this is set,
                    # so it is set however the teardown above ends.
                    record["exit_handled"] = True
                    self._notify_background_changed()

        capture_thread = threading.Thread(target=capture_output, daemon=True)
        record["capture_thread"] = capture_thread
        with self._process_condition:
            self._processes[process_id] = record
            self._process_condition.notify_all()
        capture_thread.start()
        self._notify_process_status()
        self._schedule_activity_flush()
        return {**self._process_info(record), "success": True}

    def inspect_processes(
        self, process_id: str | None = None, log_tail_chars: int = 0
    ) -> dict[str, Any]:
        """Return process status and an optional bounded log tail."""
        if log_tail_chars < 0 or log_tail_chars > MAX_PROCESS_LOG_TAIL_CHARS:
            return {
                "error": f"log_tail_chars must be between 0 and {MAX_PROCESS_LOG_TAIL_CHARS}",
                "success": False,
            }
        if log_tail_chars and process_id is None:
            return {
                "error": "process_id is required when log_tail_chars is non-zero",
                "success": False,
            }

        record = None
        if process_id is not None:
            with self._process_lock:
                record = self._find_process_record_locked(process_id)
            if record is None:
                return {"error": f"Unknown managed process: {process_id}", "success": False}

        if record is not None:
            records = [record]
        else:
            with self._process_lock:
                records = list(self._processes.values())
        processes = [self._process_info(item) for item in records]
        if log_tail_chars:
            log_path = self._resolve_path(processes[0]["log_path"])
            size = log_path.stat().st_size
            byte_limit = log_tail_chars * 4
            start = max(0, size - byte_limit)
            with log_path.open("rb") as handle:
                if start > 0:
                    handle.seek(start - 1)
                    starts_mid_line = handle.read(1) not in (b"\r", b"\n")
                else:
                    starts_mid_line = False
                handle.seek(start)
                raw = handle.read()
            # Decode complete lines with the legacy-codepage fallback. The first
            # line may be a UTF-8 fragment because the bounded read starts at a
            # byte offset, so decode that fragment without reinterpreting it.
            lines = raw.split(b"\n")
            decoded_lines = [
                line.decode("utf-8", errors="replace")
                if index == 0 and starts_mid_line
                else decode_output_text(line)
                for index, line in enumerate(lines)
            ]
            decoded = "\n".join(decoded_lines)
            processes[0]["log_tail"] = decoded[-log_tail_chars:]
            processes[0]["log_tail_truncated"] = start > 0 or len(decoded) > log_tail_chars
        truncated = bool(log_tail_chars and processes[0].get("log_tail_truncated"))
        result = {
            "processes": processes,
            "count": len(processes),
            "truncated": truncated,
            "success": True,
        }
        if truncated:
            result["truncation_reason"] = "log tail limit"
            result["guidance"] = "Request a different log tail, or read the log file in focused slices."

        if len(json.dumps(result, indent=2)) > MAX_TOOL_OUTPUT_CHARS:
            result["truncated"] = True
            result["truncation_reason"] = "character cap"
            result["guidance"] = "Inspect one process at a time or read its log file in focused slices."
            if log_tail_chars:
                tail = processes[0]["log_tail"]
                overflow = len(json.dumps(result, indent=2)) - MAX_TOOL_OUTPUT_CHARS
                processes[0]["log_tail"] = tail[overflow:]
                processes[0]["log_tail_truncated"] = True
            while processes and len(json.dumps(result, indent=2)) > MAX_TOOL_OUTPUT_CHARS:
                processes.pop()
            result["count"] = len(processes)
        return result

    def _inspect_processes(
        self, process_id: str | None = None, log_tail_chars: int = 0
    ) -> dict[str, Any]:
        return self.inspect_processes(process_id, log_tail_chars)

    @staticmethod
    def _is_watcher(record: dict[str, Any]) -> bool:
        """Whether a process may still notify the agent.

        Pending until the capture thread has posted the exit notification, so
        a waiter cannot observe the exit before the notification exists.
        """
        return (
            record["notify"] != "none"
            and not record["stop_requested"]
            and not record["exit_handled"]
        )

    def has_pending_notifications(self) -> bool:
        """Whether a running process or scheduled wakeup may still notify."""
        with self._process_lock:
            if any(self._is_watcher(record) for record in self._processes.values()):
                return True
        return self.pending_wakeups() > 0

    def background_counts(self) -> tuple[int, int]:
        """Return ``(watchers, background)``.

        Watchers are processes and wakeups that may still notify the agent;
        background processes are running ones that never will.
        """
        with self._process_lock:
            records = list(self._processes.values())
        watchers = background = 0
        for record in records:
            if self._is_watcher(record):
                watchers += 1
            elif self._process_info(record)["status"] == "running":
                background += 1
        return watchers + self.pending_wakeups(), background

    @staticmethod
    def _stop_process_record(record: dict[str, Any]) -> None:
        # Deliberate stops do not notify; only unexpected exits do. Once this
        # returns, no notification from the process can still be in flight.
        with record["notify_lock"]:
            record["stop_requested"] = True
        with record["state_lock"]:
            proc = record["process"]
        if proc is None:
            record["capture_thread"].join(timeout=5)
            return
        backend = record["process_backend"]
        if backend == "windows_job":
            with record["job_lock"]:
                job_handle = record["job_handle"]
                if job_handle is not None:
                    _close_windows_job(job_handle, terminate=True)
                    record["job_handle"] = None
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        elif backend == "linux_supervisor":
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=7)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
        elif proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
        record["capture_thread"].join(timeout=5)

    def _stop_process(self, process_id: str) -> dict[str, Any]:
        """Stop one managed process and its process tree."""
        with self._process_lock:
            record = self._processes.get(process_id)
        if record is None:
            return {"error": f"Unknown managed process: {process_id}", "success": False}
        self._stop_process_record(record)
        self._notify_process_status()
        return {**self._process_info(record), "success": True}

    def shutdown_processes(self, clear: bool = False) -> None:
        """Stop all managed processes and pending wakeups.

        Records are optionally forgotten. Nothing stopped here notifies.
        """
        self.cancel_wakeups()
        with self._process_notify_lock:
            timer, self._activity_flush_timer = self._activity_flush_timer, None
        if timer is not None:
            timer.cancel()
        with self._process_lock:
            records = list(self._processes.values())
        for record in records:
            self._stop_process_record(record)
        if clear:
            with self._process_lock:
                self._processes.clear()
        self._notify_process_status()
