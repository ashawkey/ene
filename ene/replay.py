"""Shared compact transcript replay selection."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class HiddenMessages:
    """An omission marker positioned within a compact replay."""

    count: int


def hidden_message(count: int) -> str:
    """Format the compact replay omission marker."""
    noun = "message" if count == 1 else "messages"
    return f"{count} {noun} hidden"


def compact_replay(
    items: Sequence[T],
    *,
    is_user: Callable[[T], bool],
    is_assistant: Callable[[T], bool],
    has_text: Callable[[T], bool],
    is_visible: Callable[[T], bool] | None = None,
) -> list[T | HiddenMessages]:
    """Retain every direct user/assistant message and mark hidden activity.

    User messages are always direct transcript content. Assistant messages are
    retained when they contain visible text; tool-only and reasoning-only
    assistant records are folded with other activity. Items for which
    ``is_visible`` is false are omitted without contributing to the marker.
    """
    replay: list[T | HiddenMessages] = []
    hidden = 0
    for item in items:
        direct = is_user(item) or (is_assistant(item) and has_text(item))
        if direct:
            if hidden:
                replay.append(HiddenMessages(hidden))
                hidden = 0
            replay.append(item)
        elif is_visible is None or is_visible(item):
            hidden += 1
    if hidden:
        replay.append(HiddenMessages(hidden))
    return replay
