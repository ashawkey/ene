"""Context management for ene: tool-result filtering and compaction.

Two layers of context window management:
1. Tool-result ingress — compact oversized results before they reach history.
2. Compaction — near the end of the window, replace the whole history with an
   LLM-written handoff, keeping only the in-flight round and a small recent tail.

History is otherwise append-only, so every request reuses the provider's cached
prefix. Rewriting old messages in place — trimming stale tool results once the
window was half full — was tried and dropped: each pass re-billed the whole
prefix, and holding usage near half the window cost more per round than letting
it grow and handing off once. Usage is measured against the last real token
count reported by the API.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, replace
from typing import Any, Callable

from ene.messages import ContentPart, ImagePart, Message, TextPart, ToolCall
from ene.ui import AgentConsole
from ene.utils.interrupt import RequestInterrupted


# Base64 bytes are not tokens. Leave room below common 32 MB HTTP body limits
# for text, tool schemas, and provider framing. This is not a total-body limit.
MAX_IMAGE_PAYLOAD_BYTES = 24 * 1024 * 1024


def _inline_image_bytes(part: ContentPart) -> int:
    if not isinstance(part, ImagePart):
        return 0
    url = part.image_url
    if isinstance(url, dict):
        url = url.get("url")
    if not isinstance(url, str) or not url.startswith("data:"):
        return 0
    return len(url) if url.isascii() else len(url.encode("utf-8"))


def _image_payload_sizes(messages: list[Message]) -> list[int]:
    return [size for message in messages if isinstance(message.content, list)
            for part in message.content if (size := _inline_image_bytes(part))]


def limit_image_payload(messages: list[Message], max_bytes: int) -> list[Message]:
    """Project out oldest inline images without changing the saved history."""
    sizes = _image_payload_sizes(messages)
    remaining = sum(sizes)
    if remaining <= max_bytes:
        return messages
    if sizes[-1] > MAX_IMAGE_PAYLOAD_BYTES:
        raise RuntimeError(
            "The newest image exceeds the request image-byte budget; "
            "resize or compress it, then retry in a new session (/clear)."
        )
    # A budget learned from older, smaller images must not prevent sending a
    # new, larger image. Always keep the newest, subject to the default cap.
    max_bytes = max(max_bytes, sizes[-1])
    result = []
    for message in messages:
        if remaining <= max_bytes or not isinstance(message.content, list):
            result.append(message)
            continue
        parts = []
        for part in message.content:
            size = _inline_image_bytes(part)
            if size and remaining > max_bytes:
                remaining -= size
                parts.append(TextPart(
                    "[Older image omitted to fit the request byte limit; "
                    "use read_image to inspect it again if needed.]"
                ))
            else:
                parts.append(part)
        result.append(replace(message, content=parts))
    return result


class ContextManager:
    """Flat conversation history with context-management hooks.

    Messages are :class:`~ene.messages.Message` objects; the OpenAI wire
    format only exists at the provider and persistence boundaries.
    """

    def __init__(self, system_prompt: str):
        self.system_prompt = Message.system(system_prompt)
        self.messages: list[Message] = []
        self.image_payload_budget = MAX_IMAGE_PAYLOAD_BYTES
        # Facts compaction has already summarized the evidence away for; see
        # CompactionState. Empty until the first pass.
        self.compaction_state = CompactionState()
        # Running character total of ``self.messages`` (excluding the system
        # prompt, matching estimate_context_chars). Maintained incrementally on
        # append and invalidated (None) on any bulk mutation.
        self._char_cache: int | None = 0

    @property
    def estimated_chars(self) -> int:
        """Cached character total of the history, recomputed only when stale."""
        if self._char_cache is None:
            self._char_cache = estimate_context_chars(self.messages)
        return self._char_cache

    @property
    def image_count(self) -> int:
        return sum(message.image_count for message in self.messages)

    @property
    def total_chars(self) -> int:
        """Character total of the whole prompt payload, system prompt included.

        This is the basis token estimates are anchored on, so it must match what
        :meth:`get` actually sends. The system prompt is mutated in place on
        persona and skill changes, hence the live ``len`` rather than a cache.
        """
        return len(self.system_prompt.text) + self.estimated_chars

    def add(self, message: Message) -> None:
        self.messages.append(message)
        if self._char_cache is not None:
            self._char_cache += message.chars

    def drop_last(self, message: Message) -> bool:
        """Withdraw *message* if it is still the newest one. Returns whether it was.

        Identity-checked so a caller can only undo its own append: a turn that
        has already moved on must not lose someone else's message.
        """
        if not self.messages or self.messages[-1] is not message:
            return False
        self.messages.pop()
        if self._char_cache is not None:
            self._char_cache -= message.chars
        return True

    def get(
        self, include_system: bool = True, *, include_images: bool = True,
    ) -> list[Message]:
        messages: list[Message] = []
        if include_system:
            messages.append(self.system_prompt)
        messages.extend(self.messages)
        if not include_images:
            # Project a text-only request without discarding saved image bytes.
            messages = [
                message.with_text(
                    (message.text + "\n[Images omitted for a text-only model.]").lstrip()
                ) if message.image_count else message
                for message in messages
            ]
        else:
            messages = limit_image_payload(messages, self.image_payload_budget)
        return messages

    def shrink_image_payload(self) -> bool:
        """Reduce sent image bytes after a body-size rejection, keeping the newest."""
        sizes = _image_payload_sizes(self.get())
        if len(sizes) <= 1:
            return False
        self.image_payload_budget = max(sizes[-1], sum(sizes) // 2)
        return True

    def replace_messages(self, new_messages: list[Message]) -> None:
        # A same-object call is a no-op (content unchanged) so the cache stays
        # valid; only a genuine replacement invalidates it.
        if new_messages is not self.messages:
            self.messages = list(new_messages)
            self._char_cache = None


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CHARS_PER_TOKEN = 3.3  # conservative for code-heavy workloads
# Planning allowance, not a provider billing formula. Image costs depend on
# model, resolution, and detail; encoded byte length is not a token count.
ESTIMATED_IMAGE_TOKENS = 2_048

# Layer 1: how much of the window one tool result may occupy, as
# (window ratio, floor, ceiling). Proactive tools are called repeatedly and can
# be re-run with a narrower argument, so they get the tighter budget; anything
# else — including skill tools — cannot be re-narrowed and gets more.
#
# Every ceiling must stay strictly under the tools' own MAX_TOOL_OUTPUT_CHARS
# (24_000). A ceiling at that cap can never be exceeded, which silently turns
# ingress compaction into dead code for the tool rather than making it generous.
PROACTIVE_RESULT_BUDGET = (0.02, 2_000, 6_000)
GENERIC_RESULT_BUDGET = (0.06, 4_000, 16_000)
# Tools that earn a tier of their own. A file read is content the model asked
# for by name and usually needs whole: truncating it buys a re-read round trip
# and risks an edit against a string that was cut away, so it keeps the widest
# budget. Fetched pages and process snapshots are verbose and low-density, so
# they sit between the tiers instead of at the tool cap.
RESULT_BUDGET_OVERRIDES = {
    "read_file": (0.06, 4_000, 16_000),
    "web_fetch": (0.04, 3_000, 10_000),
    "inspect_processes": (0.04, 3_000, 10_000),
}

# Upper bound on the raw capture fed into the semantic reducers. Well above any
# ingress budget so diagnostics keep their surrounding context, but small enough
# that a pathological multi-MiB capture is not scanned in full (the reducers only
# ever emit a small excerpt and never surface the middle of a huge log).
COMPACTION_INPUT_MAX_CHARS = 512_000

PROACTIVE_TOOLS = frozenset({
    "read_file", "exec_command", "inspect_processes", "ls", "glob_files", "grep_files",
    "web_fetch", "web_search",
})
PREFIX_TOOLS = frozenset({
    "read_file", "web_fetch", "ls", "glob_files", "grep_files", "web_search",
})

_ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_PROGRESS_RE = re.compile(r"^\s*(?:[\[\]=\->#|. ]{5,}\d*%?|[⠀-⣿]+)\s*$")

_TOOL_RESULT_GUIDANCE = {
    "read_file": "Use grep_files to locate relevant lines, or read_file with a narrower offset/limit.",
    "exec_command": "Search the saved output with grep_files/read_file, or rerun with quiet flags or a targeted filter.",
    "ls": "Use glob_files with a narrower pattern instead of listing the whole directory.",
    "glob_files": "Use a narrower glob pattern or base_dir.",
    "grep_files": "Use a narrower regex, path, or file_glob.",
    "web_fetch": "Search the saved text or fetch a more specific source.",
    "web_search": "Refine the query before requesting more results.",
}

# Compaction threshold. A flat ratio leaves too little room on a small window
# and compacts absurdly early on a very large one, so the trigger also reserves
# space for the model's own output plus the compaction round-trip.
COMPACTION_RATIO = 0.85
COMPACTION_RESERVE_TOKENS = 8_000
# The output share of that reserve is capped: a reply rarely uses a 128k output
# cap, and reserving all of it dragged gpt-5's trigger down to half its window.
# A request whose cap would overrun the window gets a smaller one instead (see
# request_output_tokens), and a reply cut short is continued automatically.
COMPACTION_OUTPUT_RESERVE_MAX_TOKENS = 32_000
COMPACTION_MIN_TRIGGER_RATIO = 0.5  # floor, for tiny windows
# Ceiling on the summarization response, and the allowance used when deciding
# whether a pass can free enough: the summary is the one part of a compaction
# that adds context back. Generous because reasoning tokens count against the
# same budget, and a truncated handoff is worse than a slightly long one.
COMPACTION_SUMMARY_MAX_TOKENS = 8_000

# Summarization input budget, as (window ratio, floor, ceiling) in characters.
# The raw history is discarded entirely, so the summarizer gets a generous view
# on a large window; the ceiling keeps it well inside a smaller summary model.
COMPACTION_SUMMARY_INPUT = (0.15, 50_000, 200_000)

# Compaction is a full handoff: every message is replaced by one summary,
# except the newest rounds, kept verbatim up to this slice of the window. The
# round in flight (a tool call whose results the model has not yet seen, or a
# prompt it has not yet answered) is always kept, whatever its size.
COMPACTION_KEEP_RECENT_RATIO = 0.05
COMPACTION_KEEP_RECENT_MAX_TOKENS = 12_000
# The final reply that ended the last turn is kept verbatim when this small, so
# a follow-up like "do option 2" still resolves after an idle compaction.
COMPACTION_LAST_REPLY_MAX_CHARS = 16_000

# Below this yield a compaction is considered ineffective.
COMPACTION_MIN_YIELD_RATIO = 0.05
# Growth required before retrying after any compaction attempt.
COMPACTION_RETRY_GROWTH_RATIO = 0.10


# ---------------------------------------------------------------------------
# Self-calibrating token estimator
# ---------------------------------------------------------------------------


class TokenEstimator:
    """Self-calibrating chars-per-token estimator anchored on real usage.

    Starts with a conservative default and refines via exponential moving
    average each time real token counts are observed from the API. The most
    recent observation is also kept as an anchor: character counting cannot see
    tool schemas, per-message wire framing, or provider-side prompt additions,
    so only the *delta* since that observation is estimated. Images have a
    separate planning allowance and never calibrate the text ratio.
    """

    EMA_ALPHA = 0.15  # smoothing factor (higher = faster adaptation)
    MIN_RATIO = 2.0
    MAX_RATIO = 6.0

    def __init__(self, initial: float = DEFAULT_CHARS_PER_TOKEN):
        self._ratio = initial
        self._calibrated = False
        self._anchor: tuple[int, int, int] | None = None  # (chars, tokens, images)

    @property
    def chars_per_token(self) -> float:
        return self._ratio

    @property
    def anchored(self) -> bool:
        return self._anchor is not None

    def calibrate(self, prompt_chars: int, prompt_tokens: int) -> None:
        """Update the ratio from an observed (chars, tokens) pair."""
        if prompt_tokens < 100:  # too few tokens to be meaningful
            return
        observed = prompt_chars / prompt_tokens
        observed = max(self.MIN_RATIO, min(self.MAX_RATIO, observed))
        if not self._calibrated:
            self._ratio = observed
            self._calibrated = True
        else:
            self._ratio = (1 - self.EMA_ALPHA) * self._ratio + self.EMA_ALPHA * observed

    def observe(self, prompt_chars: int, prompt_tokens: int, image_count: int = 0) -> None:
        """Anchor on real usage without calibrating text ratios on image costs."""
        if prompt_tokens <= 0:
            return
        if not image_count:
            self.calibrate(prompt_chars, prompt_tokens)
        self._anchor = (prompt_chars, prompt_tokens, image_count)

    def prompt_tokens(self, prompt_chars: int, image_count: int = 0) -> int:
        """Estimate the prompt tokens a request of *prompt_chars* would cost.

        *prompt_chars* must be measured on the same basis as :meth:`observe`
        (system prompt included), otherwise the anchor and the delta disagree.
        """
        estimate = self.chars_to_tokens(prompt_chars) + image_count * ESTIMATED_IMAGE_TOKENS
        if self._anchor is None:
            return estimate
        anchor_chars, anchor_tokens, anchor_images = self._anchor
        if image_count < anchor_images:
            # The provider did not report per-image costs. After removing
            # images, re-estimate rather than subtracting an invented cost
            # from measured usage (which can even make the result negative).
            return estimate
        return max(0, anchor_tokens + self.chars_to_tokens(prompt_chars - anchor_chars)
                   + (image_count - anchor_images) * ESTIMATED_IMAGE_TOKENS)

    def chars_to_tokens(self, chars: int) -> int:
        """Convert a character count to an estimated token count."""
        return int(chars / self._ratio) if self._ratio > 0 else chars


# ---------------------------------------------------------------------------
# Message-level helpers (messages are typed; the wire format lives in
# ene.messages.Message.to_wire / from_wire)
# ---------------------------------------------------------------------------


def estimate_context_chars(messages: list[Message]) -> int:
    """Text/state characters only, for text ratio calibration."""
    return sum(message.chars for message in messages)


def _message_budget_chars(message: Message, chars_per_token: float) -> float:
    """Text plus an image allowance in the policy's character-budget units."""
    return message.chars + message.image_count * ESTIMATED_IMAGE_TOKENS * chars_per_token


def _context_budget_chars(messages: list[Message], chars_per_token: float) -> float:
    return sum(_message_budget_chars(message, chars_per_token) for message in messages)


# ---------------------------------------------------------------------------
# Layer 1: Tool-result ingress
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolResultEnvelope:
    """Tool result plus the invocation that produced it."""

    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    text: str

    @property
    def original_chars(self) -> int:
        return self.result.get("original_output_chars", len(self.text))


@dataclass(frozen=True)
class ToolCompaction:
    text: str
    original_chars: int
    retained_chars: int
    artifact_path: str | None = None


def tool_result_char_budget(
    context_length: int,
    chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
    tool_name: str = "",
) -> int:
    """Return the maximum characters one tool result may add to history."""
    ratio, min_chars, max_chars = RESULT_BUDGET_OVERRIDES.get(
        tool_name,
        PROACTIVE_RESULT_BUDGET if tool_name in PROACTIVE_TOOLS else GENERIC_RESULT_BUDGET,
    )
    if context_length <= 0:
        return max_chars
    return max(min_chars, min(int(context_length * ratio * chars_per_token), max_chars))


def _normalize_exec_output(text: str) -> str:
    """Remove terminal control sequences and low-signal progress output."""
    text = _ANSI_RE.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines: list[str] = []
    blank = False
    for line in text.split("\n"):
        if _PROGRESS_RE.fullmatch(line):
            continue
        if not line.strip():
            if blank:
                continue
            blank = True
        else:
            blank = False
        lines.append(line.rstrip())
    normalized = "\n".join(lines).strip("\n")
    if text.endswith("\n"):
        normalized += "\n"
    return normalized


def _collapse_repeated_lines(text: str) -> str:
    """Collapse consecutive duplicate log lines without hiding unique events."""
    lines = text.splitlines(keepends=True)
    if len(lines) < 3:
        return text

    result: list[str] = []
    i = 0
    while i < len(lines):
        j = i + 1
        while j < len(lines) and lines[j] == lines[i]:
            j += 1
        result.append(lines[i])
        repeats = j - i
        if repeats > 1:
            result.append(f"[previous line repeated {repeats - 1} more times]\n")
        i = j
    return "".join(result)


def _sample_edges(text: str, available: int) -> str:
    """Sample line-aligned head and tail excerpts within *available* chars."""
    label = "\n[... middle omitted ...]\n"
    if available <= len(label):
        return label[:available]

    content_budget = available - len(label)
    head_budget = content_budget // 2
    tail_budget = content_budget - head_budget

    head = text[:head_budget]
    newline = head.rfind("\n", int(head_budget * 0.6))
    if newline >= 0:
        head = head[:newline]

    tail = text[-tail_budget:]
    newline = tail.find("\n", 0, int(tail_budget * 0.4))
    if newline >= 0:
        tail = tail[newline + 1:]
    return f"{head}{label}{tail}"


def _sample_prefix(text: str, available: int) -> str:
    """Keep one contiguous prefix so a subsequent focused call is unambiguous."""
    label = "\n[... remainder omitted ...]\n"
    if available <= len(label):
        return label[:available]
    limit = available - len(label)
    prefix = text[:limit]
    newline = prefix.rfind("\n", int(limit * 0.7))
    if newline >= 0:
        prefix = prefix[:newline]
    return f"{prefix}{label}"


def _sample_suffix(text: str, available: int) -> str:
    """Keep one contiguous suffix for logs and other latest-first output."""
    label = "[... earlier output omitted ...]\n"
    if available <= len(label):
        return label[-available:]
    limit = available - len(label)
    suffix = text[-limit:]
    newline = suffix.find("\n", 0, int(limit * 0.3))
    if newline >= 0:
        suffix = suffix[newline + 1:]
    return f"{label}{suffix}"


def _compact_process_output(result: dict[str, Any], text: str, available: int) -> str:
    """Keep process status metadata plus the latest requested log tail.

    Falls back to *text*, the formatted result, when the result is not the
    expected shape.
    """
    processes = result.get("processes")
    if not isinstance(processes, list):
        return _sample_suffix(text, available)

    status = [
        {key: value for key, value in process.items() if key != "log_tail"}
        for process in processes
    ]
    status_text = json.dumps({"processes": status}, indent=2)
    tail = next(
        (
            process["log_tail"]
            for process in processes
            if isinstance(process, dict) and process.get("log_tail")
        ),
        "",
    )
    if not tail:
        return _sample_prefix(status_text, available)

    status_header = "[process status]\n"
    tail_header = "\n\n[latest log tail]\n"
    content_budget = available - len(status_header) - len(tail_header)
    if content_budget <= 0:
        return _sample_suffix(tail, available)
    tail_budget = min(len(tail), int(content_budget * 0.65))
    status_budget = content_budget - tail_budget
    return (
        status_header
        + _sample_prefix(status_text, status_budget)
        + tail_header
        + _sample_suffix(tail, tail_budget)
    )


def _excerpt_for(
    tool_name: str, result: dict[str, Any], text: str, budget: int
) -> str:
    """Excerpt *text* from whichever end carries the signal.

    The full output is already on disk under a pointer, so an excerpt only has
    to be enough to decide whether to go and read it — which end it comes from
    is the only thing worth getting right. A document or listing is read from the
    top, a process snapshot keeps its status and latest tail, and anything else
    is sampled at both ends.

    Command output is deliberately *not* tail-only: stdout and stderr share one
    pipe, and a piped child block-buffers stdout while stderr stays unbuffered,
    so a diagnostic routinely lands ahead of the bulk output it describes.
    """
    if tool_name in PREFIX_TOOLS:
        return _sample_prefix(text, budget)
    if tool_name == "inspect_processes":
        return _compact_process_output(result, text, budget)
    return _sample_edges(text, budget)


def _compaction_footer(
    original_tokens: int,
    retained_tokens: int,
    location: str,
    warning: str,
    guidance: str,
) -> str:
    saved_percent = max(0, min(99, round((1 - retained_tokens / original_tokens) * 100)))
    return (
        f"\n[compacted: ~{original_tokens:,}→~{retained_tokens:,} tokens, "
        f"-{saved_percent}%.{location}{warning} {guidance}]"
    )


def _fit_with_footer(body: str, footer: str, budget: int) -> str:
    """Fit content while preserving the complete recovery footer."""
    if len(footer) >= budget:
        raise ValueError("Tool-result metadata exceeds the compaction budget")
    return f"{body[:budget - len(footer)]}{footer}"


def compact_tool_result_envelope(
    envelope: ToolResultEnvelope,
    context_length: int,
    chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
    artifact_path: str | None = None,
) -> ToolCompaction:
    """Compact a tool result with invocation-aware deterministic reducers.

    Call only for a result that exceeds :func:`tool_result_char_budget`; the
    caller already computes that budget to decide whether to save an artifact.
    """
    tool_name = envelope.tool_name
    raw_text = envelope.text
    budget = tool_result_char_budget(context_length, chars_per_token, tool_name)
    original_chars = envelope.original_chars
    # Preserve the first-level truncation notice (which records the true
    # pre-cap size) so prefix-sampled compaction keeps an accurate recovery
    # hint. Only prefix tools append this line at the very end; latest/edge
    # tools reconstruct their own metrics from the full artifact.
    trailing_notice = ""
    if tool_name in PREFIX_TOOLS:
        last_line = raw_text.rstrip().rsplit("\n", 1)[-1]
        if last_line.startswith("[output truncated:"):
            trailing_notice = f"\n{last_line}"

    text = raw_text
    if tool_name == "exec_command":
        text = _collapse_repeated_lines(_normalize_exec_output(raw_text))

    guidance = _TOOL_RESULT_GUIDANCE.get(
        tool_name,
        "Use a narrower tool call or read the captured output in slices.",
    )
    if artifact_path:
        display_path = artifact_path
        if len(display_path) > 512:
            display_path = f"...{display_path[-509:]}"
        location = f" Captured output: {display_path}."
    else:
        location = " Captured output could not be saved."
        guidance = guidance.replace(
            "Search the saved output with grep_files/read_file, or ",
            "",
        ).replace(
            "Search the saved text or ",
            "",
        )

    header = f"[Large {tool_name[:80]} result compacted: {original_chars:,} characters]\n"
    warning_text = (
        " Warning: recovery artifact reached its 100 MiB capture limit."
        if envelope.result.get("artifact_truncated")
        else ""
    )
    original_tokens = max(1, int(original_chars / chars_per_token))
    footer_reserve = len(_compaction_footer(
        original_tokens, original_tokens, location, warning_text, guidance,
    ))
    content_budget = budget - len(header) - len(trailing_notice) - footer_reserve

    excerpt = _excerpt_for(tool_name, envelope.result, text, content_budget)
    body = f"{header}{excerpt}{trailing_notice}"
    retained_tokens = max(1, int((len(body) + footer_reserve) / chars_per_token))
    footer = _compaction_footer(
        original_tokens, retained_tokens, location, warning_text, guidance
    )
    compacted = _fit_with_footer(body, footer, budget)
    return ToolCompaction(
        text=compacted,
        original_chars=original_chars,
        retained_chars=len(compacted),
        artifact_path=artifact_path,
    )


# ---------------------------------------------------------------------------
# Tool-call helpers
# ---------------------------------------------------------------------------


def _parse_call_arguments(tool_call: ToolCall) -> dict[str, Any]:
    try:
        arguments = json.loads(tool_call.arguments or "{}")
    except (json.JSONDecodeError, TypeError):
        arguments = {}
    return arguments if isinstance(arguments, dict) else {}


def build_tool_name_index(messages: list[Message]) -> dict[str, str]:
    """Build a tool_call_id → tool_name lookup from all assistant messages."""
    return {
        tool_call.id: tool_call.name
        for message in messages if message.is_assistant
        for tool_call in message.tool_calls or []
        if tool_call.id and tool_call.name
    }


# Tools whose call changes a file; compaction lists their targets as modified.
_WRITE_TOOLS = frozenset({"write_file", "edit_file", "multi_edit", "remove_file"})

# Argument shown next to a tool name so a summary can still tell which call it
# was: the file it read, the command it ran, the URL it fetched.
_DIGEST_ARGS = ("file", "path", "command", "url", "query", "pattern")


def _call_digest(arguments: dict[str, Any]) -> str:
    for key in _DIGEST_ARGS:
        value = arguments.get(key)
        if value:
            text = str(value).replace("\n", " ")
            return text if len(text) <= 120 else f"{text[:117]}..."
    return ""


# ---------------------------------------------------------------------------
# Layer 3: Compaction
# ---------------------------------------------------------------------------
#
# A pass is a full handoff, not a sliding window: the whole history is replaced
# by one summary message, and only the newest rounds survive verbatim. Keeping
# the most recent half of the window used to leave a long session compacting
# again a few rounds later, re-summarizing the same material each time; a full
# handoff pays the cache invalidation once and buys back nearly the whole window.
#
# What a summary cannot afford to paraphrase is carried exactly instead: the
# user's own messages, the files and skills touched, and a pointer to a verbatim
# transcript on disk, so a detail the summary dropped can still be looked up.

SUMMARY_MARKER = "[Previous conversation summary]"

# Headings the emitted summary uses. The deterministic ones are rebuilt from
# CompactionState on every pass rather than trusted to survive re-summarization.
_ORIGINAL_REQUEST_HEADING = "## Original request"
_USER_MESSAGES_HEADING = "## Later user messages"
_FILES_HEADING = "## Files"
_SKILLS_HEADING = "## Loaded skills"
_TRANSCRIPT_HEADING = "## Full transcript"

# Per-message excerpt limits when building the summarization input. The total
# is capped by COMPACTION_SUMMARY_INPUT, and recency is handled by
# _fit_summary_input dropping from the middle.
_SUMMARY_MESSAGE_CHARS = 2_000
_SUMMARY_TOOL_RESULT_CHARS = 1_000

# A prior summary stands in for everything before it, so it is passed to the
# summarizer separately with a far larger budget than an ordinary message.
_PRIOR_SUMMARY_CHAR_LIMIT = 16_000

# The original request is reproduced verbatim rather than re-summarized.
_FIRST_REQUEST_CHAR_LIMIT = 4_000

# Later user messages are carried verbatim too: they are the cheapest and most
# authoritative record of intent, and a correction such as "no, keep the old
# API" is exactly what a paraphrase loses. The newest messages win the budget.
_USER_MESSAGE_CHAR_LIMIT = 4_000
_USER_MESSAGES_CHAR_BUDGET = 32_000

# Files are carried as a list, never as contents: re-reading them here is what
# makes compaction refill the window and cascade into compacting again.
_SUMMARY_FILE_LIST_LIMIT = 25
_TRANSCRIPT_LIST_LIMIT = 10

_SUMMARY_FORMAT = """\
## Goal
[What the user is trying to accomplish. Several items if the session covered several tasks.]

## Constraints & Preferences
- [Requirements, preferences, or constraints the user stated, or "(none)"]

## Progress
### Done
- [Work that is finished, with the files and functions it touched]
### In Progress
- [What is being worked on right now, and its exact current state]
### Blocked
- [Anything preventing progress, or "(none)"]

## Key Decisions
- **[Decision]**: [Why it was made]

## Errors & Fixes
- [Errors hit, how each was resolved, and approaches that failed, or "(none)"]

## Next Steps
1. [Ordered list of what should happen next]

## Critical Context
- [Data, values, commands and their latest results, or references needed to continue, or "(none)"]"""

_SUMMARY_RULES = """\
The raw history is discarded once you answer: this summary, the user's own
messages, and a list of touched files are all the next assistant will see.
Do not copy the user's messages out again; record what was done and learned.
Preserve exact file paths, function and class names, commands, and error text
verbatim — never paraphrase an identifier. Keep each section concise."""

_COMPACTION_PROMPT = """\
You are compacting a conversation between a user and an AI coding assistant.
Write a handoff summary that another assistant will use to continue this work
without access to the raw history.

Use this EXACT section structure:

{sections}

{rules}

<conversation>
{conversation}
</conversation>"""

_COMPACTION_UPDATE_PROMPT = """\
You are updating an existing handoff summary with newer conversation history.

Rules:
- PRESERVE every still-relevant fact from the previous summary. The raw history
  behind it is already gone and cannot be recovered if you drop it.
- ADD progress, decisions, and context from the new messages.
- MOVE items from "In Progress" to "Done" as they complete, and update
  "Next Steps" to match the current state.
- REMOVE only what is genuinely obsolete.

Use this EXACT section structure:

{sections}

{rules}

<previous-summary>
{previous}
</previous-summary>

<conversation>
{conversation}
</conversation>"""


def compaction_trigger_tokens(context_length: int, max_output_tokens: int = 0) -> int:
    """Token usage at which compaction has to run.

    Reserves room for the model's own output (capped, see
    COMPACTION_OUTPUT_RESERVE_MAX_TOKENS) plus the compaction round-trip on top
    of a plain ratio, so the trigger stays safe on a small window without firing
    absurdly early on a very large one.
    """
    reserve = (
        min(max_output_tokens, COMPACTION_OUTPUT_RESERVE_MAX_TOKENS)
        + COMPACTION_RESERVE_TOKENS
    )
    return int(max(
        context_length * COMPACTION_MIN_TRIGGER_RATIO,
        min(context_length * COMPACTION_RATIO, context_length - reserve),
    ))


# Room kept free past a request's estimated prompt when its output cap has to
# shrink to fit the window: the prompt estimate is calibrated, not exact.
REQUEST_OUTPUT_SAFETY_RATIO = 0.02
MIN_REQUEST_OUTPUT_TOKENS = 4_096


def request_output_tokens(
    context_length: int, max_output_tokens: int, prompt_tokens: int
) -> int:
    """Output cap for one request: the configured cap, unless it overruns the window.

    The compaction trigger reserves only part of a large output cap, so near the
    trigger prompt plus cap can exceed the window — which providers that count
    both against it (vLLM, Anthropic) reject outright. Shrinking the cap instead
    costs at most a reply cut short, and those are continued automatically.
    """
    if context_length <= 0:
        return max_output_tokens
    room = context_length - prompt_tokens - int(context_length * REQUEST_OUTPUT_SAFETY_RATIO)
    return max(
        min(max_output_tokens, room),
        min(max_output_tokens, MIN_REQUEST_OUTPUT_TOKENS),
    )


def needs_compaction(
    messages: list[Message],
    context_length: int,
    chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
    used_tokens: int | None = None,
    max_output_tokens: int = 0,
) -> bool:
    if context_length <= 0:
        return False
    if used_tokens is None:
        used_tokens = int(_context_budget_chars(messages, chars_per_token) / chars_per_token)
    return used_tokens > compaction_trigger_tokens(context_length, max_output_tokens)


def _safe_split_index(messages: list[Message], idx: int) -> int:
    """Walk backward so the split never lands inside a tool-call / result pair."""
    while idx > 0 and messages[idx].is_tool:
        idx -= 1
    return idx


def _is_summary(msg: Message) -> bool:
    return msg.is_user and msg.text.startswith(SUMMARY_MARKER)


def _in_flight_start(messages: list[Message]) -> int:
    """Start of the tail a pass must keep whatever its size.

    Everything after the newest assistant message is input the model has not
    responded to yet — tool results it asked for, a new prompt, a notification
    — and a summary written without the model having seen it would drop it.
    The newest assistant message is kept with it: its tool calls are what those
    results answer, and a short final reply is what a follow-up prompt refers to.
    A reply that is the very last message is kept whatever its size: it may be
    one cut off by the output limit and about to be continued, and a summary of
    half an answer cannot be continued from.
    """
    last = next(
        (i for i in range(len(messages) - 1, -1, -1) if messages[i].is_assistant),
        None,
    )
    if last is None:
        return 0  # nothing has been answered yet, so nothing can be summarized
    reply = messages[last]
    if (
        reply.tool_calls
        or last == len(messages) - 1
        or reply.chars <= COMPACTION_LAST_REPLY_MAX_CHARS
    ):
        return last
    return last + 1


def _tail_start(
    messages: list[Message], context_length: int, chars_per_token: float
) -> int:
    """Index from which messages survive a pass verbatim.

    The in-flight round always survives; whole earlier messages join it only
    while they fit the small recent budget, so the working set the model is in
    the middle of using does not have to be re-read immediately. Never extends
    over a previous summary, which is always replaced.
    """
    start = _in_flight_start(messages)
    if context_length <= 0:
        return start
    keep_chars = min(
        context_length * COMPACTION_KEEP_RECENT_RATIO,
        COMPACTION_KEEP_RECENT_MAX_TOKENS,
    ) * chars_per_token
    used = _context_budget_chars(messages[start:], chars_per_token)
    while start > 0:
        previous = _safe_split_index(messages, start - 1)
        if _is_summary(messages[previous]):
            break
        cost = _context_budget_chars(messages[previous:start], chars_per_token)
        if used + cost > keep_chars:
            break
        used += cost
        start = previous
    return start


def _render_summary_input(messages: list[Message]) -> list[str]:
    """Render each message for the summarizer, one entry per message.

    A prior summary is skipped: it is handed to the summarizer separately so it
    can be updated rather than re-compressed.
    """
    parts: list[str] = []
    for msg in messages:
        if _is_summary(msg):
            continue
        limit = _SUMMARY_TOOL_RESULT_CHARS if msg.is_tool else _SUMMARY_MESSAGE_CHARS

        if msg.is_system:
            parts.append(f"[System]: {msg.text[:limit]}")
        elif msg.is_inbox:
            parts.append(f"Inbox notifications: {msg.text[:limit]}")
        elif msg.is_user:
            parts.append(f"User: {msg.text[:limit]}")
        elif msg.is_assistant:
            if msg.text:
                parts.append(f"Assistant: {msg.text[:limit]}")
            for tool_call in msg.tool_calls or []:
                digest = _call_digest(_parse_call_arguments(tool_call))
                call = f"{tool_call.name}({digest})" if digest else tool_call.name
                parts.append(f"  [Called tool: {call}]")
        elif msg.is_tool:
            snippet = msg.text[:limit] + "..." if len(msg.text) > limit else msg.text
            parts.append(f"  [Tool result]: {snippet}")
    return parts


def _fit_summary_input(parts: list[str], budget: int) -> str:
    """Fit rendered messages into *budget*, dropping from the middle.

    Truncating the tail would discard the most recent state — exactly what the
    summary exists to carry forward — so the oldest and newest messages are
    kept and the middle is elided. The first entry is always retained because
    it anchors what the session set out to do.
    """
    if sum(len(part) + 1 for part in parts) <= budget:
        return "\n".join(parts)

    marker = "\n[... middle of the conversation omitted ...]\n"
    head = [parts[0]]
    tail: list[str] = []
    used = len(marker) + len(parts[0]) + 1
    low, high = 1, len(parts) - 1

    step = 0
    while low <= high:
        # Two of every three entries come from the newest end.
        from_tail = step % 3 != 2
        index = high if from_tail else low
        cost = len(parts[index]) + 1
        if used + cost > budget:
            break
        if from_tail:
            tail.insert(0, parts[index])
            high -= 1
        else:
            head.append(parts[index])
            low += 1
        used += cost
        step += 1

    return "\n".join(head) + marker + "\n".join(tail)


def summary_input_budget(
    context_length: int, chars_per_token: float = DEFAULT_CHARS_PER_TOKEN
) -> int:
    """Characters of rendered history the summarizer may see in one pass."""
    ratio, floor, ceiling = COMPACTION_SUMMARY_INPUT
    if context_length <= 0:
        return floor
    return int(max(floor, min(context_length * ratio * chars_per_token, ceiling)))


def render_transcript(messages: list[Message]) -> str:
    """Render *messages* verbatim, for the on-disk record a pass leaves behind."""
    names = build_tool_name_index(messages)
    blocks: list[str] = []
    for msg in messages:
        if _is_summary(msg):
            label = "Earlier compaction summary"
        elif msg.is_inbox:
            label = "Inbox notification"
        elif msg.is_user:
            label = "User"
        elif msg.is_assistant:
            label = "Assistant"
        elif msg.is_tool:
            name = names.get(msg.tool_call_id or "", "unknown tool")
            label = f"Tool result: {name} ({msg.tool_call_id})"
        else:
            label = msg.role.capitalize()
        lines = [f"### {label}"]
        if msg.text:
            lines.append(msg.text)
        if msg.image_count:
            lines.append(f"[{msg.image_count} image(s) not included in this transcript]")
        for tool_call in msg.tool_calls or []:
            lines.append(f"[Tool call {tool_call.id}: {tool_call.name} {tool_call.arguments}]")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def _merge_recent(previous: list[str], current: list[str], limit: int) -> list[str]:
    """Combine two last-touch-ordered lists, newest last, bounded in length."""
    merged = {name: None for name in previous}
    for name in current:
        merged.pop(name, None)
        merged[name] = None
    return list(merged)[-limit:]


def _collect_tool_usage(messages: list[Message]) -> tuple[list[str], list[str], list[str]]:
    """Return (files read, files modified, skills loaded) in last-touch order."""
    read: dict[str, None] = {}
    modified: dict[str, None] = {}
    skills: dict[str, None] = {}

    for msg in messages:
        if not msg.is_assistant:
            continue
        for tool_call in msg.tool_calls or []:
            name = tool_call.name
            arguments = _parse_call_arguments(tool_call)
            if name == "load_skill":
                skill = str(arguments.get("name", ""))
                if skill:
                    skills.pop(skill, None)
                    skills[skill] = None
                continue
            path = str(arguments.get("file", ""))
            if not path:
                continue
            if name in _WRITE_TOOLS:
                read.pop(path, None)
                modified.pop(path, None)
                modified[path] = None
            elif name == "read_file" and path not in modified:
                read.pop(path, None)
                read[path] = None

    return list(read), list(modified), list(skills)


def _clip_user_message(text: str) -> str:
    text = text.strip()
    if len(text) <= _USER_MESSAGE_CHAR_LIMIT:
        return text
    note = "\n[... message truncated ...]"
    return text[:_USER_MESSAGE_CHAR_LIMIT - len(note)] + note


def _bound_user_messages(messages: list[str]) -> tuple[list[str], int]:
    """Keep the newest messages that fit the budget; return them and the count dropped."""
    kept: list[str] = []
    used = 0
    for text in reversed(messages):
        if used + len(text) > _USER_MESSAGES_CHAR_BUDGET:
            break
        kept.append(text)
        used += len(text)
    kept.reverse()
    return kept, len(messages) - len(kept)


@dataclass(frozen=True)
class CompactionState:
    """Facts that have to outlive the messages they were derived from.

    Compaction deletes the whole history, so a later pass can no longer see who
    asked for what or which files were touched. Carrying that as data — rather
    than writing it into the summary prose and parsing it back out on the next
    pass — keeps it exact: a model cannot drop a path or a user's message it
    never had to reproduce, and a heading it invents cannot shadow ours.
    """

    original_request: str = ""
    # User messages after the original request, verbatim, oldest first.
    user_messages: tuple[str, ...] = ()
    omitted_user_messages: int = 0
    # The model-written part of the latest handoff, updated by the next pass.
    summary: str = ""
    read_files: tuple[str, ...] = ()
    modified_files: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    # Verbatim transcripts of compacted history, oldest first.
    transcripts: tuple[str, ...] = ()

    def absorb(self, messages: list[Message]) -> CompactionState:
        """Fold the messages about to be summarized into the carried state."""
        read, modified, skills = _collect_tool_usage(messages)
        modified_files = _merge_recent(
            list(self.modified_files), modified, _SUMMARY_FILE_LIST_LIMIT
        )
        written = set(modified_files)

        inputs = [
            msg for msg in messages if msg.is_user_input and not _is_summary(msg)
        ]
        original_request = self.original_request
        if not original_request and inputs:
            # The first user turn defines the task, and only the first: a later
            # pass must not relabel the session with a mid-session instruction.
            original_request = inputs.pop(0).text[:_FIRST_REQUEST_CHAR_LIMIT]
        later = [_clip_user_message(msg.display) for msg in inputs]
        user_messages, dropped = _bound_user_messages(
            list(self.user_messages) + [text for text in later if text]
        )

        return replace(
            self,
            original_request=original_request,
            user_messages=tuple(user_messages),
            omitted_user_messages=self.omitted_user_messages + dropped,
            read_files=tuple(
                path for path in _merge_recent(
                    list(self.read_files), read, _SUMMARY_FILE_LIST_LIMIT
                )
                if path not in written
            ),
            modified_files=tuple(modified_files),
            skills=tuple(_merge_recent(list(self.skills), skills, _SUMMARY_FILE_LIST_LIMIT)),
        )

    def handoff(self, summary: str, transcript: str | None) -> CompactionState:
        """Record the summary a pass wrote and where its transcript was saved."""
        transcripts = self.transcripts
        if transcript:
            transcripts = tuple(
                _merge_recent(list(transcripts), [transcript], _TRANSCRIPT_LIST_LIMIT)
            )
        return replace(self, summary=summary, transcripts=transcripts)

    def render_requests(self) -> list[str]:
        """Render what the user asked for, verbatim, as leading sections."""
        lines: list[str] = []
        if self.original_request:
            lines += ["", _ORIGINAL_REQUEST_HEADING, self.original_request]
        if self.user_messages:
            note = "Verbatim, oldest first"
            if self.omitted_user_messages:
                note += f"; {self.omitted_user_messages} older message(s) omitted"
            lines += ["", _USER_MESSAGES_HEADING, f"{note}."]
            for text in self.user_messages:
                lines += ["", "\n".join(f"> {line}" for line in text.splitlines())]
        return lines

    def render(self) -> list[str]:
        """Render the carried facts as trailing sections of a summary message."""
        lines: list[str] = []
        if self.read_files or self.modified_files:
            lines += ["", _FILES_HEADING]
            if self.modified_files:
                lines.append(f"- Modified: {', '.join(self.modified_files)}")
            if self.read_files:
                lines.append(f"- Read: {', '.join(self.read_files)}")
            lines.append("- Contents are not included; re-read a file before relying on it.")
        if self.skills:
            lines += [
                "",
                _SKILLS_HEADING,
                f"- Active: {', '.join(self.skills)}",
                "- Call load_skill again if you need the full instructions.",
            ]
        if self.transcripts:
            lines += [
                "",
                _TRANSCRIPT_HEADING,
                f"- Saved verbatim, oldest first: {', '.join(self.transcripts)}",
                "- When a detail you need is missing here, search it with grep_files "
                "or read_file a slice instead of guessing.",
            ]
        return lines


def _build_summary_message(state: CompactionState) -> Message:
    """Assemble the replacement message, carried sections included."""
    lines = [
        SUMMARY_MARKER,
        "The earlier conversation was compacted. This is a system-generated "
        "handoff, not a new instruction from the user.",
    ]
    lines += state.render_requests()
    if state.summary:
        lines += ["", state.summary]
    lines += state.render()
    return Message.user("\n".join(lines))


def _yields_enough(
    to_compact: list[Message],
    state: CompactionState,
    context_length: int,
    chars_per_token: float,
) -> bool:
    """Whether replacing *to_compact* with a handoff is worth the round-trip.

    What a pass really frees is the compacted messages *minus* the handoff it
    writes back: the carried sections, measured exactly, plus the summary at its
    maximum length. A pass that nets less than COMPACTION_MIN_YIELD_RATIO is not
    worth a summarization request or the cache invalidation it causes.
    """
    freed = _context_budget_chars(to_compact, chars_per_token)
    written_back = (
        _build_summary_message(state).chars
        + COMPACTION_SUMMARY_MAX_TOKENS * chars_per_token
    )
    min_yield = context_length * COMPACTION_MIN_YIELD_RATIO * chars_per_token
    return freed - written_back >= min_yield


def compact_context(
    messages: list[Message],
    summarize: Callable[[str], str],
    console: AgentConsole | None = None,
    context_length: int = 0,
    chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
    state: CompactionState = CompactionState(),
    save_transcript: Callable[[str], str | None] | None = None,
) -> tuple[list[Message], CompactionState]:
    """Replace the history with one LLM-written handoff message.

    Everything except the in-flight round and a small recent slice (see
    :func:`_tail_start`) is summarized away. *messages* should **not** include
    the system prompt (it is managed separately by ContextManager). *summarize*
    receives the prepared prompt and returns summary text through the active
    provider. *state* carries the facts earlier passes summarized away; the
    caller keeps the one returned. *save_transcript*, when given, stores the
    verbatim compacted history and returns a path the handoff can point to.

    Returns a new list, or *messages* itself when no compaction happened —
    nothing old enough to summarize, too little to be worth the round-trip, or a
    failed summarization. Callers rely on that identity to tell a pass that
    shrank the context from one that changed nothing.
    """
    split_index = _tail_start(messages, context_length, chars_per_token)
    to_compact = messages[:split_index]
    if not any(message.is_assistant or message.is_tool for message in to_compact):
        # Nothing the model did precedes the in-flight round: only a previous
        # summary or user messages, which are carried verbatim anyway. A pass
        # would cost a round-trip and free nothing.
        return messages, state

    absorbed = state.absorb(to_compact)
    if context_length > 0 and not _yields_enough(
        to_compact, absorbed, context_length, chars_per_token
    ):
        return messages, state

    previous = state.summary or next(
        (message.text for message in to_compact if _is_summary(message)), ""
    )
    conversation = _fit_summary_input(
        _render_summary_input(to_compact),
        summary_input_budget(context_length, chars_per_token),
    )
    if previous:
        prompt = _COMPACTION_UPDATE_PROMPT.format(
            sections=_SUMMARY_FORMAT,
            rules=_SUMMARY_RULES,
            previous=previous[:_PRIOR_SUMMARY_CHAR_LIMIT],
            conversation=conversation,
        )
    else:
        prompt = _COMPACTION_PROMPT.format(
            sections=_SUMMARY_FORMAT,
            rules=_SUMMARY_RULES,
            conversation=conversation,
        )

    def warn(text: str) -> None:
        if console is not None:
            console.warn(text, exc_info=True)
        else:
            print(f"[WARNING] {text}", file=sys.stderr)

    try:
        summary = summarize(prompt).strip()
    except RequestInterrupted:
        raise  # the user cancelled; the caller unwinds, nothing failed
    except Exception as exc:
        warn(
            f"Context compaction LLM call failed ({type(exc).__name__}: {exc}); "
            "preserving context"
        )
        return messages, state

    transcript = None
    if save_transcript is not None:
        try:
            transcript = save_transcript(render_transcript(to_compact))
        except (OSError, ValueError, RuntimeError) as exc:
            # The handoff stands on its own; it just cannot point at a record.
            warn(f"Could not save the compacted transcript ({exc})")

    state = absorbed.handoff(summary, transcript)
    return [_build_summary_message(state)] + messages[split_index:], state
