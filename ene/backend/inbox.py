"""Render queued inbox notifications as one conversation message."""

from __future__ import annotations

import re
import time
from html import escape

from ene.tools.process_manager import WATCHER_GLYPH
from ene.utils.io import InboxEvent

INBOX_HEADER = (
    "[Inbox: asynchronous notifications delivered by the harness, not typed by "
    "the user. Act on them within the current task; end your turn when nothing "
    "needs doing.]"
)
# Appended to a one-shot run's last delivery: the reply becomes its result.
FINAL_DELIVERY_NOTE = (
    "[Nothing else is pending: no managed process can notify you again. Your "
    "reply to this message ends the run and is returned as its final result, "
    "so give the complete final answer to the original task, not only a "
    "reaction to these events.]"
)
_DISPLAY_LINE_CHARS = 160
_DISPLAY_LABEL_CHARS = 40
# Event text is untrusted (process output, external clients), so tags that
# delimit the inbox structure are neutralized inside it.
_STRUCTURE_TAG_RE = re.compile(r"<(?=/?(?:event|dropped)\b)", re.IGNORECASE)


def _neutralize(text: str) -> str:
    return _STRUCTURE_TAG_RE.sub("&lt;", text)


def _display_source(source: str) -> str:
    """Show ``process:5 build`` like the process status: ``↻ 5 [build]``."""
    if not source.startswith("process:"):
        return f"[{source}]"
    process_id, _, label = source[len("process:"):].partition(" ")
    if len(label) > _DISPLAY_LABEL_CHARS:
        label = label[: _DISPLAY_LABEL_CHARS - 1] + "…"
    return f"{WATCHER_GLYPH} {process_id}" + (f" [{label}]" if label else "")


def idle_timeout_notice(seconds: float) -> str:
    """Notification that ends a one-shot run's wait for background work."""
    return (
        f"No notification arrived for {seconds:g} seconds, so the harness stopped "
        "every managed process. Their results will not arrive."
    )


def format_inbox_message(
    events: list[InboxEvent],
    dropped: dict[str, int] | None = None,
    *,
    final: bool = False,
) -> tuple[str, str]:
    """Return ``(model_text, display_text)`` for a delivered batch.

    *final* marks a one-shot run's last delivery, whose reply is its result.
    """
    blocks = [INBOX_HEADER]
    display = []
    for event in events:
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(event.created_at))
        blocks.append(
            f'<event source="{escape(event.source)}" time="{stamp}">\n'
            f"{_neutralize(event.text)}\n</event>"
        )
        first = " ".join(event.text.split())
        if len(first) > _DISPLAY_LINE_CHARS:
            first = first[: _DISPLAY_LINE_CHARS - 1] + "…"
        display.append(f"{_display_source(event.source)} {first}")
    for source, count in sorted((dropped or {}).items()):
        note = f"{count} older event(s) from {source} were dropped because the inbox overflowed."
        blocks.append(f"<dropped>{escape(note)}</dropped>")
        display.append(f"{_display_source(source)} {count} older event(s) dropped")
    if final:
        blocks.append(FINAL_DELIVERY_NOTE)
    return "\n\n".join(blocks), "\n".join(display)
