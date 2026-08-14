"""Typed conversation messages for ene.

The agent's history is a list of :class:`Message` objects. Dicts in the
OpenAI wire format exist only at the two boundaries that speak it:

* ingress — provider responses are normalized into :class:`Message` objects
  in :mod:`ene.utils.streaming` and the provider adapters;
* egress — providers serialize :class:`Message` objects back to wire dicts
  right before sending an HTTP request, and
  :class:`~ene.session_store.SessionStore` stores ``Message.to_wire()`` so the
  on-disk format stays the wire format.

:meth:`Message.to_wire` and :meth:`Message.from_wire` round-trip losslessly:
every key the parser does not know is kept in ``extra`` and re-emitted
verbatim, so a stored message loaded from disk serializes back to the exact
dict it was written from.

Messages that have entered the history are never mutated in place — rewrites
(compaction, eviction, text replacement) build new objects via
:func:`dataclasses.replace`, so ``id()`` stays a valid identity for a message
for the lifetime of the context it lives in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Union

# ---------------------------------------------------------------------------
# Content parts
# ---------------------------------------------------------------------------


@dataclass
class TextPart:
    """A plain-text part of a multimodal user message."""

    text: str


@dataclass
class ImagePart:
    """An image part of a multimodal user message.

    ``image_url`` is kept verbatim (a ``{"url": ...}`` dict or a bare URL
    string) so serialization reproduces whatever the caller stored.
    """

    image_url: Any


@dataclass
class RawPart:
    """A content part the parser does not model, kept verbatim for fidelity."""

    raw: Any


ContentPart = Union[TextPart, ImagePart, RawPart]


def _part_from_wire(item: Any) -> ContentPart:
    if not isinstance(item, dict):
        return RawPart(item)
    kind = item.get("type")
    if kind == "text":
        text = item.get("text")
        if isinstance(text, str):
            return TextPart(text)
        return RawPart(item)
    if kind == "image_url":
        if "image_url" in item:
            return ImagePart(item["image_url"])
        return RawPart(item)
    return RawPart(item)


def _part_to_wire(part: ContentPart) -> Any:
    if isinstance(part, TextPart):
        return {"type": "text", "text": part.text}
    if isinstance(part, ImagePart):
        return {"type": "image_url", "image_url": part.image_url}
    return part.raw


# ---------------------------------------------------------------------------
# Tool calls
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """One tool invocation the model requested.

    ``arguments`` is the raw JSON string exactly as the model produced it —
    parsing happens at the caller (tool dispatch) so the original text is
    never lost to a re-serialization round-trip.
    """

    id: str
    name: str
    arguments: str
    extra: dict[str, Any] = field(default_factory=dict)

    def parse_arguments(self) -> dict[str, Any]:
        """Parse ``arguments`` as JSON, raising on malformed input.

        An empty string is malformed, matching the wire contract where a tool
        call without arguments is ``"{}"`` rather than absent.
        """
        return json.loads(self.arguments)

    def to_wire(self) -> dict[str, Any]:
        """Serialize to the OpenAI function-call wire format."""
        function: dict[str, Any] = {"name": self.name, "arguments": self.arguments}
        function.update(self.extra.get("function", {}))
        wire: dict[str, Any] = {
            "id": self.id,
            "type": "function",
            "function": function,
        }
        # "function" is a canonical key the merged dict already carries; any
        # other unknown key is re-emitted verbatim.
        wire.update(
            {key: value for key, value in self.extra.items() if key != "function"}
        )
        return wire

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> ToolCall:
        """Parse one wire-format tool call, keeping unknown keys in ``extra``."""
        call = cls(id="", name="", arguments="")
        for key, value in data.items():
            if key == "id" and isinstance(value, str):
                call.id = value
            elif key == "function" and isinstance(value, dict):
                name = value.get("name")
                arguments = value.get("arguments")
                if isinstance(name, str):
                    call.name = name
                if isinstance(arguments, str):
                    call.arguments = arguments
                for extra_key, extra_value in value.items():
                    if extra_key not in ("name", "arguments"):
                        call.extra.setdefault("function", {})[extra_key] = extra_value
            elif key not in ("type",):
                call.extra[key] = value
        return call


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

ROLE_SYSTEM = "system"
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"


@dataclass
class Message:
    """One conversation message.

    ``role`` is one of the ``ROLE_*`` constants. ``content`` is plain text,
    a list of content parts (multimodal user messages), or ``None``.
    ``display_content`` carries the user-facing form when it differs from what
    the model sees. ``provider_state`` is opaque provider state (OpenAI Codex
    response items) replayed verbatim on continuation requests.
    """

    role: str
    content: str | list[ContentPart] | None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    display_content: str | None = None
    provider_state: dict[str, Any] | None = None
    reasoning_content: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    # -- constructors -------------------------------------------------------

    @classmethod
    def system(cls, content: str) -> Message:
        """Build a system message."""
        return cls(role=ROLE_SYSTEM, content=content)

    @classmethod
    def user(
        cls,
        content: str | list[ContentPart] | None,
        *,
        display_content: str | None = None,
    ) -> Message:
        """Build a user message, optionally with a user-facing display form."""
        return cls(
            role=ROLE_USER, content=content, display_content=display_content
        )

    @classmethod
    def assistant(
        cls,
        content: str | None = None,
        *,
        tool_calls: list[ToolCall] | None = None,
        provider_state: dict[str, Any] | None = None,
        reasoning_content: str | None = None,
    ) -> Message:
        """Build an assistant message."""
        return cls(
            role=ROLE_ASSISTANT,
            content=content,
            tool_calls=tool_calls,
            provider_state=provider_state,
            reasoning_content=reasoning_content,
        )

    @classmethod
    def tool(
        cls,
        tool_call_id: str,
        content: str,
        *,
        display_content: str | None = None,
    ) -> Message:
        """Build a tool-result message answering one tool call."""
        return cls(
            role=ROLE_TOOL,
            content=content,
            tool_call_id=tool_call_id,
            display_content=display_content,
        )

    # -- role checks --------------------------------------------------------

    @property
    def is_system(self) -> bool:
        return self.role == ROLE_SYSTEM

    @property
    def is_user(self) -> bool:
        return self.role == ROLE_USER

    @property
    def is_assistant(self) -> bool:
        return self.role == ROLE_ASSISTANT

    @property
    def is_tool(self) -> bool:
        return self.role == ROLE_TOOL

    # -- derived access -----------------------------------------------------

    @property
    def text(self) -> str:
        """Concatenated text content, or ``""`` for non-text content."""
        content = self.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                part.text for part in content if isinstance(part, TextPart)
            )
        return ""

    @property
    def display(self) -> str:
        """User-facing text: ``display_content`` when present, else ``text``."""
        return self.display_content if isinstance(self.display_content, str) else self.text

    @property
    def chars(self) -> int:
        """Rough character cost of the message, matching the wire payload.

        Assistant tool-call arguments count against the message, and an
        assistant message carrying provider state costs at least the size of
        the replayed state, because providers resend that instead of the
        canonical projection.
        """
        chars = len(self.text)
        if self.is_assistant:
            for tool_call in self.tool_calls or []:
                chars += len(tool_call.arguments)
            if self.provider_state:
                state_chars = len(
                    json.dumps(self.provider_state, ensure_ascii=False)
                )
                chars = max(chars, state_chars)
        return chars

    def with_text(self, text: str) -> Message:
        """Copy of this message with ``content`` replaced by plain *text*."""
        return replace(self, content=text)

    # -- wire boundary ------------------------------------------------------

    def to_wire(self) -> dict[str, Any]:
        """Serialize to the OpenAI wire format used on disk and on the wire."""
        content: Any = self.content
        if isinstance(content, list):
            content = [_part_to_wire(part) for part in content]
        wire: dict[str, Any] = {"role": self.role, "content": content}
        if self.tool_calls is not None:
            wire["tool_calls"] = [call.to_wire() for call in self.tool_calls]
        if self.tool_call_id is not None:
            wire["tool_call_id"] = self.tool_call_id
        if self.display_content is not None:
            wire["display_content"] = self.display_content
        if self.provider_state is not None:
            wire["provider_state"] = self.provider_state
        if self.reasoning_content is not None:
            wire["reasoning_content"] = self.reasoning_content
        wire.update(self.extra)
        return wire

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> Message:
        """Parse one wire-format message.

        Keys the model does not know — including a malformed value for a key
        it does know — land in ``extra`` and are re-emitted by
        :meth:`to_wire`, so the round-trip is lossless.
        """
        message = cls(role="", content=None)
        for key, value in data.items():
            if key == "role" and isinstance(value, str):
                message.role = value
            elif key == "content":
                if isinstance(value, str) or value is None:
                    message.content = value
                elif isinstance(value, list):
                    message.content = [_part_from_wire(item) for item in value]
                else:
                    message.extra[key] = value
            elif key == "tool_calls" and isinstance(value, list) and all(
                isinstance(call, dict) for call in value
            ):
                message.tool_calls = [ToolCall.from_wire(call) for call in value]
            elif key == "tool_call_id" and isinstance(value, str):
                message.tool_call_id = value
            elif key == "display_content" and isinstance(value, str):
                message.display_content = value
            elif key == "provider_state" and isinstance(value, dict):
                message.provider_state = value
            elif key == "reasoning_content" and isinstance(value, str):
                message.reasoning_content = value
            else:
                message.extra[key] = value
        return message
