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
    is_turn_start: Callable[[T], bool] | None = None,
    user_starts_turn: Callable[[T], bool] | None = None,
    is_continuation_user: Callable[[T], bool] | None = None,
    starts_mid_turn: bool = False,
) -> list[T | HiddenMessages]:
    """Return all user prompts and final replies, marking omitted messages in place.

    When ``is_turn_start`` is supplied, a user item is retained only if a turn
    marker follows it before the next user item. ``user_starts_turn`` can mark
    already-replayed transcript prompts as valid without a marker.
    ``is_continuation_user`` identifies steering messages that belong to the
    current turn rather than starting a new one. This lets event replay omit
    displayed slash commands while preserving input injected between tool
    iterations. Items for which ``is_visible`` is false are omitted without
    being counted. ``starts_mid_turn`` preserves the final assistant item from
    a bounded history whose prompt has already been evicted.
    """
    turns: list[tuple[int | None, int | None, list[int]]] = []
    user_index: int | None = None
    assistant_index: int | None = None
    continuation_indices: list[int] = []
    started = starts_mid_turn or is_turn_start is None
    partial_turn = starts_mid_turn

    def finish() -> None:
        if started and (user_index is not None or partial_turn):
            turns.append((user_index, assistant_index, continuation_indices))

    for index, item in enumerate(items):
        if is_user(item):
            if (
                user_index is not None
                and is_continuation_user is not None
                and is_continuation_user(item)
            ):
                continuation_indices.append(index)
                continue
            finish()
            user_index = index
            assistant_index = None
            continuation_indices = []
            partial_turn = False
            started = is_turn_start is None or bool(
                user_starts_turn is not None and user_starts_turn(item)
            )
        elif user_index is not None or partial_turn:
            if is_turn_start is not None and is_turn_start(item):
                started = True
            elif is_assistant(item) and has_text(item):
                assistant_index = index
    finish()

    retained = {
        index
        for prompt_index, response_index, continuation_indexes in turns
        for index in (prompt_index, *continuation_indexes, response_index)
        if index is not None
    }
    replay: list[T | HiddenMessages] = []
    hidden = 0
    for index, item in enumerate(items):
        if index in retained:
            if hidden:
                replay.append(HiddenMessages(hidden))
                hidden = 0
            replay.append(item)
        elif is_visible is None or is_visible(item):
            hidden += 1
    if hidden:
        replay.append(HiddenMessages(hidden))
    return replay
