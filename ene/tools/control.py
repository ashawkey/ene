"""Agent-loop control tools."""

import math
import threading
import time
from typing import Any, Callable

MAX_WAKEUP_SECONDS = 7 * 24 * 3600
# Each pending wakeup holds a timer thread.
MAX_PENDING_WAKEUPS = 100


class ControlToolsMixin:
    # Delivers ``(text, source)`` to the agent's inbox; installed by the agent.
    inbox_sink: Callable[[str, str], Any] | None = None

    def _init_wakeups(self) -> None:
        self._wakeups: dict[str, dict[str, Any]] = {}
        # Reentrant: a sink may query pending wakeups while one is being posted.
        self._wakeup_lock = threading.RLock()
        self._next_wakeup_number = 1

    def _schedule_wakeup(self, seconds: float, note: str) -> dict[str, Any]:
        """Post an inbox notification after *seconds* without blocking."""
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
            raise ValueError("seconds must be a number")
        seconds = float(seconds)
        if not math.isfinite(seconds) or seconds <= 0 or seconds > MAX_WAKEUP_SECONDS:
            raise ValueError(
                f"seconds must be greater than zero and at most {MAX_WAKEUP_SECONDS}"
            )
        if not isinstance(note, str) or not note.strip():
            raise ValueError("note must be a non-empty string")
        sink = self.inbox_sink
        if sink is None:
            return {"error": "Wakeups are unavailable in this session.", "success": False}
        note = note.strip()
        with self._wakeup_lock:
            if len(self._wakeups) >= MAX_PENDING_WAKEUPS:
                return {
                    "error": f"At most {MAX_PENDING_WAKEUPS} wakeups may be pending.",
                    "success": False,
                }
            wakeup_id = f"w{self._next_wakeup_number}"
            self._next_wakeup_number += 1

        def fire() -> None:
            # Post under the lock: a waiter never sees the wakeup gone before
            # its notification is queued, and cancellation never races a post.
            with self._wakeup_lock:
                if wakeup_id not in self._wakeups:
                    return
                try:
                    sink(f"Scheduled wakeup {wakeup_id} fired: {note}", f"wakeup:{wakeup_id}")
                finally:
                    self._wakeups.pop(wakeup_id, None)
            self._notify_process_status()

        scheduled_at = time.time()
        due_at = scheduled_at + seconds
        timer = threading.Timer(seconds, fire)
        timer.daemon = True
        with self._wakeup_lock:
            self._wakeups[wakeup_id] = {
                "timer": timer,
                "due_at": due_at,
                "scheduled_at": scheduled_at,
                "note": note,
            }
        timer.start()
        self._notify_process_status()
        return {
            "wakeup_id": wakeup_id,
            "due_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(due_at)),
            "success": True,
        }

    def pending_wakeups(self) -> int:
        with self._wakeup_lock:
            return len(self._wakeups)

    def wakeup_activity(self) -> list[dict[str, Any]]:
        """Pending wakeups as process-like rows for status displays."""
        now = time.time()
        with self._wakeup_lock:
            items = list(self._wakeups.items())
        return [
            {
                "wakeup_id": wakeup_id,
                "note": wakeup["note"],
                "due_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(wakeup["due_at"])),
                "due_in_seconds": max(0.0, wakeup["due_at"] - now),
                "elapsed_seconds": max(0.0, now - wakeup["scheduled_at"]),
            }
            for wakeup_id, wakeup in items
        ]

    def cancel_wakeups(self) -> None:
        """Discard every pending wakeup (session end or reset)."""
        with self._wakeup_lock:
            wakeups, self._wakeups = self._wakeups, {}
        for wakeup in wakeups.values():
            wakeup["timer"].cancel()
        if wakeups:
            self._notify_process_status()
