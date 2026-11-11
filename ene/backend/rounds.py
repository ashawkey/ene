"""Per-round usage statistics, recorded by the agent and saved with the session.

A round is one agent turn (a user message, a queued notification, or
``/continue``). Its record holds wall time split into model and tool time, the
token deltas of the session counters, streaming latency (mean time to first
token and output tokens per second), and tool activity. Every terminal or web session records rounds the
same way, because this runs inside the agent rather than in a client.

Persistence: each session revision stores only the *latest* round in its state
(``last_round``), so the delta-encoded history grows by one record per round.
The full list is rebuilt by walking a revision's ancestors
(:meth:`ene.session_store.SessionStore.round_history`), which makes rewinds and
forks show exactly the rounds that led to the checked-out point.
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from ene.messages import ORIGIN_INBOX, ROLE_USER


MAX_ROUND_HISTORY = 500
PROMPT_PREVIEW = 120
# Generation shorter than this is too short to time (a buffered response).
MIN_DECODE_SECONDS = 0.05
_TOKEN_KEYS = (
    ("input_tokens", "prompt"),
    ("cached_tokens", "cached_prompt"),
    ("output_tokens", "completion"),
    ("reasoning_tokens", "reasoning"),
)


class OutputRateMeter:
    """Live output tokens/s estimate for one streaming model request.

    Providers report exact usage only when a stream ends, so the live rate
    converts streamed characters (text, reasoning, tool-call arguments) to
    estimated tokens, averaged since the first generated delta. *publish*
    receives the rate at most every :attr:`INTERVAL` seconds.
    """

    INTERVAL = 0.5
    # Below this, one buffered burst would read as an absurd rate.
    MIN_SECONDS = 0.5

    def __init__(
        self,
        chars_to_tokens: Callable[[int], int],
        publish: Callable[[float], None],
        clock: Callable[[], float] = time.monotonic,
    ):
        self._chars_to_tokens = chars_to_tokens
        self._publish = publish
        self._clock = clock
        self._chars = 0
        self._first: float | None = None
        self._published = 0.0

    def add(self, chars: int) -> None:
        if chars <= 0:
            return
        now = self._clock()
        if self._first is None:
            self._first = self._published = now
        self._chars += chars
        elapsed = now - self._first
        if elapsed <= 0 or elapsed < self.MIN_SECONDS or now - self._published < self.INTERVAL:
            return
        self._published = now
        try:
            self._publish(round(self._chars_to_tokens(self._chars) / elapsed, 1))
        except Exception:
            pass  # a display side channel must never fail the stream


def _message_preview(message: Any) -> tuple[str, str]:
    """``(kind, preview)`` for the user-role message that opened a round."""
    if getattr(message, "origin", None) == ORIGIN_INBOX:
        kind = "notification"
    else:
        kind = "user"
    text = message.display_content if message.display_content else message.content
    if isinstance(text, list):
        text = " ".join(
            str(getattr(part, "text", "")) for part in text if getattr(part, "text", None)
        )
    return kind, " ".join(str(text or "").split())[:PROMPT_PREVIEW]


class RoundStatsMixin:
    """Records one statistics entry per agent round (see module docstring)."""

    def _round_state(self) -> dict[str, Any]:
        state = getattr(self, "_round_stats_state", None)
        if state is None:
            state = {"history": [], "base": [], "current": None, "last": None, "depth": 0}
            self._round_stats_state = state
        return state

    @property
    def round_history(self) -> list[dict[str, Any]]:
        return list(self._round_state()["history"])

    def _round_opener(self) -> tuple[str, str]:
        messages = getattr(getattr(self, "context", None), "messages", None) or []
        for message in reversed(messages):
            if message.role == ROLE_USER and message.display_content != "":
                return _message_preview(message)
            if message.role == ROLE_USER:
                continue  # hidden synthetic notices (peer announcements)
            break
        return "continue", ""

    def active_round(self) -> dict[str, Any] | None:
        current = self._round_state()["current"]
        return {"started_at": current["started_at"]} if current else None

    @contextmanager
    def _track_round(self) -> Iterator[None]:
        state = self._round_state()
        state["depth"] += 1
        if state["depth"] > 1:
            # Nested timers (a command inside a round) belong to the outer one.
            try:
                yield
            finally:
                state["depth"] -= 1
            return
        totals = dict(getattr(self, "token_totals", {}) or {})
        kind, prompt = self._round_opener()
        state["current"] = {
            "started_at": time.time(),
            "totals": totals,
            "kind": kind,
            "prompt": prompt,
            "tool_calls": 0,
            "tool_failures": 0,
            "edits": 0,
            "model_calls": 0,
            "model_time": 0.0,
            "tool_time": 0.0,
            "ttft_total": 0.0,
            "ttft_count": 0,
            "decode_time": 0.0,
            "decode_tokens": 0,
        }
        try:
            yield
        finally:
            state["depth"] -= 1
            current, state["current"] = state["current"], None
            try:
                self._finish_round(current)
            except Exception:
                # Statistics are a side channel; they must never fail a round.
                pass

    def _note_tool_result(self, success: bool, edits: int = 0) -> None:
        current = self._round_state()["current"]
        if current is None:
            return
        current["tool_calls"] += 1
        if not success:
            current["tool_failures"] += 1
        current["edits"] += edits

    def _note_model_call(
        self,
        elapsed: float,
        *,
        ttft: float | None = None,
        output_tokens: int = 0,
    ) -> None:
        """Record one model request: wall time, time to first token, and the
        output tokens generated after it (for tokens per second)."""
        current = self._round_state()["current"]
        if current is None:
            return
        current["model_calls"] += 1
        current["model_time"] += max(0.0, elapsed)
        if ttft is None:
            return
        ttft = max(0.0, min(ttft, elapsed))
        current["ttft_total"] += ttft
        current["ttft_count"] += 1
        decode = elapsed - ttft
        # A response delivered in one burst has no measurable generation time.
        if decode >= MIN_DECODE_SECONDS and output_tokens > 0:
            current["decode_time"] += decode
            current["decode_tokens"] += output_tokens

    def _note_tool_time(self, elapsed: float) -> None:
        current = self._round_state()["current"]
        if current is not None:
            current["tool_time"] += max(0.0, elapsed)

    def _finish_round(self, current: dict[str, Any] | None) -> None:
        if current is None:
            return
        ended = time.time()
        totals = getattr(self, "token_totals", {}) or {}
        record: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "index": int(getattr(self, "round_id", 0) or 0),
            "kind": current["kind"],
            "prompt": current["prompt"],
            "model": str(getattr(self, "model", "") or ""),
            "started_at": round(current["started_at"], 3),
            "ended_at": round(ended, 3),
            "duration": round(max(0.0, ended - current["started_at"]), 3),
            "tool_calls": current["tool_calls"],
            "tool_failures": current["tool_failures"],
            "edits": current["edits"],
            "cancelled": bool(getattr(self, "_last_interrupted", False)),
            "model_calls": current["model_calls"],
            "model_time": round(current["model_time"], 3),
            "tool_time": round(current["tool_time"], 3),
            "ttft": (
                round(current["ttft_total"] / current["ttft_count"], 3)
                if current["ttft_count"] else None
            ),
            "tps": (
                round(current["decode_tokens"] / current["decode_time"], 1)
                if current["decode_time"] > 0 else None
            ),
        }
        outcome = getattr(self, "_last_turn_outcome", None)
        record["outcome"] = str(getattr(outcome, "value", outcome or ""))
        for name, key in _TOKEN_KEYS:
            # Counters reset when a session restarts; never report negatives.
            record[name] = max(0, int(totals.get(key, 0)) - int(current["totals"].get(key, 0)))
        context_tokens = getattr(self, "_context_tokens", None)
        try:
            record["context_tokens"] = int(context_tokens()) if context_tokens else 0
        except Exception:
            record["context_tokens"] = 0
        record["context_limit"] = int(getattr(self, "context_length", 0) or 0)
        state = self._round_state()
        state["history"] = [*state["history"], record][-MAX_ROUND_HISTORY:]
        state["last"] = record
        events = getattr(self, "events", None)
        if events is not None:
            events.publish("round_stats", **record)

    # -- persistence --------------------------------------------------------

    def _round_session_data(self) -> dict[str, Any]:
        state = self._round_state()
        data: dict[str, Any] = {"last_round": state["last"]}
        if state["base"]:
            data["round_history_base"] = state["base"]
        return data

    def _restore_round_history(self, data: dict[str, Any]) -> None:
        """Adopt a materialized revision's rounds and tell clients."""
        state = self._round_state()
        history = data.get("round_history")
        state["history"] = [
            item for item in history if isinstance(item, dict)
        ][-MAX_ROUND_HISTORY:] if isinstance(history, list) else []
        base = data.get("round_history_base")
        state["base"] = list(base) if isinstance(base, list) else []
        last = data.get("last_round")
        state["last"] = last if isinstance(last, dict) else None
        self._publish_round_history()

    def _reset_round_history(self) -> None:
        state = self._round_state()
        state.update(history=[], base=[], last=None)
        self._publish_round_history()

    def _publish_round_history(self) -> None:
        events = getattr(self, "events", None)
        if events is not None:
            events.publish("round_history", rounds=self.round_history)

    def _round_usage_lines(self, limit: int = 10) -> list[str]:
        """Plain-text summary of recent rounds for ``/usage``."""
        from ene.ui import _compact_tokens as compact_tokens, _format_duration as format_duration

        history = self.round_history
        if not history:
            return []
        total_time = sum(item.get("duration", 0) for item in history)
        model_time = sum(item.get("model_time", 0) for item in history)
        tool_time = sum(item.get("tool_time", 0) for item in history)
        tools = sum(item.get("tool_calls", 0) for item in history)
        lines = [
            f"  Rounds         : {len(history)} · {format_duration(total_time)} agent time "
            f"(model {format_duration(model_time)}, tools {format_duration(tool_time)}) · {tools} tool calls",
        ]
        for item in history[-limit:]:
            hit = (
                item.get("cached_tokens", 0) / item["input_tokens"]
                if item.get("input_tokens") else 0.0
            )
            flags = " (stopped)" if item.get("cancelled") else ""
            speed = f" · {item['tps']:.0f} tok/s" if item.get("tps") else ""
            prompt = item.get("prompt") or f"[{item.get('kind', 'round')}]"
            if len(prompt) > 28:
                prompt = prompt[:27] + "…"
            lines.append(
                f"    #{item.get('index', 0):<4} {format_duration(item.get('duration', 0)):>7} "
                f"↑{compact_tokens(item.get('input_tokens', 0)):>6} ↓{compact_tokens(item.get('output_tokens', 0)):>6} "
                f"{hit:>4.0%} cached · {item.get('tool_calls', 0)} tools{speed}{flags}  {prompt}"
            )
        return lines
