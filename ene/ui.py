"""Unified user-facing I/O for ene.

All console output, confirmation prompts, and log messages go through
AgentConsole so that theming, stderr redirection (pipe mode), and
interactive prompting are handled in one place.
"""

from __future__ import annotations

import math
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Iterator

import questionary
from markdown_it import MarkdownIt
from markdown_it.common.utils import normalizeReference
from rich.cells import cell_len
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.segment import Segment
from rich.status import Status
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

from ene.models import model_icon

if TYPE_CHECKING:
    from ene.utils.io import EventHub, PromptBroker

# Custom questionary style that blends with the CLI aesthetic
_QS_STYLE = questionary.Style([
    ("qmark", "fg:ansiblue bold"),
    ("question", "fg:ansiyellow bold"),
    ("answer", "fg:ansiyellow bold"),
    ("pointer", "fg:ansicyan bold"),
    ("highlighted", "fg:ansicyan bold"),
    ("selected", "fg:ansigreen"),
    ("separator", "fg:ansiblack"),
    ("instruction", "fg:ansibrightblack"),
    ("text", "fg:ansibrightblack"),
    ("disabled", "fg:ansibrightblack italic"),
])

# Marker glyphs
_DOT = "\u2022"      # bullet •
_CHECK = "\u2713"    # ✓
_CROSS = "\u2717"    # ✗
# Brand marker: diamond-with-a-dot, the closest single-glyph emoji to the
# four-diamond logo mark. It prefixes model replies so agent speech reads
# distinctly from the •-prefixed system/tool lines.
_BRAND = "💠"  # 💠
_USER = "\u276f"     # ❯ user message header
_INBOX = "\u00bb"    # » harness notification header
_BRANCH = "\u2514"   # └ tool result under its call
_GUTTER = "\u250a"   # ┊ reasoning text
_WARN = "!"
# Headers start at column 0; everything inside a turn is indented under them.
_BODY = "  "
# Sentinel: stamp a live message with the current time. ``None`` means the
# message has no recorded time (sessions saved before timestamps existed).
_NOW: object = object()
_REFERENCE_LINK_RE = re.compile(
    r"(?<!\\)\[([^\]\n]+)\]\[([^\]\n]*)\]"
)

# drawn with ▄ █ ▀ — four diamonds around an open centre, echoing the favicon
_AGENT_LOGO = (
    "▄ ▄▄ ▄\n"
    "▄▄▀▀▄▄\n"
    "▀▀▄▄▀▀\n"
    "▀ ▀▀ ▀\n"
)

# Diff rendering: if old + new lines exceed this, show a summary instead
# Rendering width for the wide (client-wrapped) form of printed output.
WIDE_RENDER_WIDTH = 400
DIFF_MAX_LINES = 200
DIFF_PREVIEW_LINES = 10  # how many lines to show from each side in summary mode

AGENT_THEME = Theme({
    "debug": "dim cyan",
    "input": "bold yellow",
    "response": "white",
    "error": "bold red",
    "warning": "bold yellow",
    "system": "bold blue",
    "thinking": "dim italic color(245)",
    "tool": "color(244)",
    "tool_name": "bold color(75)",
    "tool_primary": "color(252)",
    "tool_meta": "color(244)",
    "tool_ok": "dim green",
    "tool_fail": "dim red",
    "header_meta": "color(244)",
    "reply_label": "bold",
    "round_summary": "color(242)",
})


def _clock(created_at: float | None) -> str:
    """Local time of day, with the date when it is not today."""
    if created_at is None:
        return ""
    local = time.localtime(created_at)
    if time.strftime("%Y-%m-%d", local) == time.strftime("%Y-%m-%d"):
        return time.strftime("%H:%M:%S", local)
    return time.strftime("%Y-%m-%d %H:%M", local)


def _stamp(created_at: object) -> float | None:
    if created_at is _NOW:
        return time.time()
    if isinstance(created_at, (int, float)) and not isinstance(created_at, bool):
        return float(created_at)
    return None


def _header(
    icon: str, label: str, label_style: str, created_at: float | None, *notes: str
) -> Text:
    """First line of a message: icon, who wrote it, time, and optional notes."""
    line = Text(f"{icon} ", style=label_style)
    line.append(label, style=label_style)
    for part in (_clock(created_at), *notes):
        if part:
            line.append(f" · {part}", style="header_meta")
    return line


class _Gutter:
    """Render a renderable beside a fixed prefix, so wrapped lines stay indented.

    *first* prefixes the first rendered line and *rest* (blank by default)
    every continuation, including soft-wrapped ones.
    """

    def __init__(self, renderable, first: str, rest: str | None = None, style: str = ""):
        self.renderable = renderable
        self.first = first
        self.rest = " " * cell_len(first) if rest is None else rest
        self.style = style

    def __rich_console__(self, console, options):
        width = max(cell_len(self.first), cell_len(self.rest))
        inner = options.update(width=max(1, options.max_width - width))
        lines = console.render_lines(self.renderable, inner, pad=False)
        style = console.get_style(self.style, default="") if self.style else None
        for index, line in enumerate(lines):
            prefix = self.first if index == 0 else self.rest
            line = _strip_padding(line)
            yield Segment(prefix if line else prefix.rstrip(" "), style)
            yield from line
            yield Segment.line()


def _strip_padding(line: list[Segment]) -> list[Segment]:
    """Drop trailing layout padding (keeping painted backgrounds, such as code
    blocks), so copied text and terminal reflow carry no trailing blanks."""
    line = list(line)
    while line:
        last = line[-1]
        if last.control or (last.style is not None and last.style.bgcolor is not None):
            break
        text = last.text.rstrip(" ")
        if text:
            line[-1] = Segment(text, last.style)
            break
        line.pop()
    return line


def _compact_tokens(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.0f}K"
    return str(value)


def _response_header(created_at: float | None, model: str | None = None) -> Text:
    return _header(_BRAND, model or "Assistant", "reply_label", created_at)


def _print_markdown_response(
    console: Console,
    content: str,
    created_at: float | None = None,
    model: str | None = None,
) -> None:
    console.print(_response_header(created_at, model))
    console.print(_Gutter(_CompactMarkdown(content), _BODY))


@dataclass(frozen=True)
class ContextStatus:
    """Context-window usage displayed alongside the thinking spinner."""

    tokens: int
    limit: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    model: str = ""

    @property
    def fraction(self) -> float:
        if self.limit <= 0:
            return 0.0
        return min(max(self.tokens / self.limit, 0.0), 1.0)

    @property
    def cache_hit_ratio(self) -> float:
        if self.input_tokens <= 0:
            return 0.0
        return min(max(self.cached_tokens / self.input_tokens, 0.0), 1.0)

    def plain(self) -> str:
        details = (
            f"↑{_compact_tokens(self.input_tokens)} · "
            f"↓{_compact_tokens(self.output_tokens)} · {self.cache_hit_ratio:.0%} hit"
        )
        if self.limit <= 0:
            return f"~{_compact_tokens(self.tokens)} · {details}"
        return f"{self.fraction:.0%} · {details}"

    def event_data(self) -> dict[str, int | str]:
        return {
            **({"model": self.model, "model_icon": model_icon(self.model)} if self.model else {}),
            "context_tokens": self.tokens,
            "context_limit": self.limit,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
        }

    def prompt_fragments(
        self, width: int = 14, *, compact: bool = False
    ) -> list[tuple[str, str]]:
        """Usage bar and details; *compact* keeps only the bar and percentage."""
        if self.limit <= 0:
            return [("class:status.detail", self.plain())]
        complete = round(width * self.fraction)
        color = (
            "high"
            if self.fraction >= 0.9
            else "medium"
            if self.fraction >= 0.75
            else "low"
        )
        return [
            (f"class:status.context.{color}", "━" * complete),
            ("class:status.track", "━" * (width - complete)),
            ("class:status.detail", f" {self.fraction:.0%}" if compact else f" · {self.plain()}"),
        ]

    def render(self) -> Table | Text:
        if self.limit <= 0:
            return Text(self.plain(), style="dim")

        color = "red" if self.fraction >= 0.9 else "yellow" if self.fraction >= 0.75 else "cyan"
        row = Table.grid(padding=(0, 1))
        row.add_row(
            ProgressBar(
                total=self.limit,
                completed=self.fraction * self.limit,
                width=14,
                style="color(238)",
                complete_style=color,
                finished_style=color,
            ),
            Text(f"{self.fraction:.0%}", style=color),
            Text(
                f"· ↑{_compact_tokens(self.input_tokens)} · "
                f"↓{_compact_tokens(self.output_tokens)} · {self.cache_hit_ratio:.0%} hit",
                style="dim",
            ),
        )
        return row


def _format_duration(seconds: float) -> str:
    total = max(0, math.ceil(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _format_latency(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    return f"{seconds:.1f}s"


def _format_round_duration(seconds: float) -> str:
    """Compact frozen elapsed time for completed parts of a round."""
    total = max(0, math.floor(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


class ThinkingIndicator:
    """Animated model activity, countdown, or indeterminate progress."""

    def __init__(
        self,
        console: Console,
        events: EventHub | None = None,
        status_suffix: str | ContextStatus = "",
        label: str = "Working",
        progress: bool = False,
        countdown: float | None = None,
        round_elapsed: float | None = None,
        initial_elapsed: float = 0.0,
        render_terminal: bool = True,
        status_sink: Callable[[list[tuple[str, str]] | None], None] | None = None,
        indicator_stack: list["ThinkingIndicator"] | None = None,
        indicator_lock: threading.RLock | None = None,
    ):
        self._console = console
        self._events = events
        self._status_suffix = status_suffix
        self._label_text = label
        self._progress = progress
        self._countdown = countdown
        self._round_elapsed = round_elapsed
        self._initial_elapsed = initial_elapsed
        self._render_terminal = render_terminal
        self._status_sink = status_sink
        self._status: Status | None = None
        self._start_time: float = 0
        self._started_at: float = 0
        self._running = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._indicator_stack = indicator_stack
        self._indicator_lock = indicator_lock
        self._tps: float | None = None
        self._ttft: float | None = None

    def __enter__(self) -> "ThinkingIndicator":
        self._start_time = time.monotonic() - self._initial_elapsed
        self._started_at = time.time()
        self._running = True
        self._stop_event.clear()
        if self._indicator_stack is None:
            self._start_visual(self._initial_elapsed)
            self._publish_start()
        else:
            assert self._indicator_lock is not None
            with self._indicator_lock:
                if self._indicator_stack:
                    self._indicator_stack[-1]._stop_visual(clear_sink=False)
                self._indicator_stack.append(self)
                self._start_visual(self._initial_elapsed)
                self._publish_start()
        if self._status_sink is not None or self._render_terminal:
            self._thread = threading.Thread(target=self._tick, daemon=True)
            self._thread.start()
        return self

    def set_status_suffix(self, status_suffix: str | ContextStatus) -> None:
        """Update the suffix of a running indicator in place.

        The next tick repaints it, so long-running work can report progress
        without stopping and restarting the indicator — a restart costs a
        thread join per update and publishes a start/stop event pair, which a
        per-item loop would multiply into a flood.
        """
        self._status_suffix = status_suffix
        if self._events is not None and self._running:
            suffix = (
                status_suffix.plain()
                if isinstance(status_suffix, ContextStatus)
                else status_suffix
            )
            self._events.publish("thinking_update", suffix=suffix)

    def set_output_rate(self, tps: float) -> None:
        """Show and publish the live output tokens/s of a streaming request."""
        self._tps = tps
        if self._events is not None and self._running:
            self._events.publish("thinking_update", tps=tps)

    def set_first_token(self, ttft: float | None = None) -> None:
        """Record the time to first token (by default, the elapsed time now)."""
        if ttft is None:
            if self._ttft is not None or not self._running:
                return
            ttft = time.monotonic() - self._start_time
        self._ttft = max(0.0, ttft)
        if self._events is not None and self._running:
            self._events.publish("thinking_update", ttft=round(self._ttft, 3))

    def __exit__(self, *args) -> None:
        self._running = False
        # Wake the tick thread so exit does not wait out its sleep interval.
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._indicator_stack is None:
            self._stop_visual()
            self._publish_stop()
            return

        assert self._indicator_lock is not None
        with self._indicator_lock:
            assert self._indicator_stack[-1] is self
            self._stop_visual(clear_sink=False)
            self._indicator_stack.pop()
            if self._indicator_stack:
                parent = self._indicator_stack[-1]
                parent._start_visual()
                parent._publish_start()
            else:
                if self._status_sink is not None:
                    self._status_sink(None)
                self._publish_stop()

    def _start_visual(self, elapsed: float | None = None) -> None:
        if elapsed is None:
            elapsed = time.monotonic() - self._start_time
        if self._status_sink is not None:
            self._status_sink(self._prompt_status("⠋", elapsed))
        elif self._render_terminal:
            self._status = Status(
                self._label(elapsed),
                spinner="dots",
                console=self._console,
                speed=1.5,
            )
            self._status.start()

    def _stop_visual(self, *, clear_sink: bool = True) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None
        if clear_sink and self._status_sink is not None:
            self._status_sink(None)

    def _publish_start(self) -> None:
        if self._events is None:
            return
        if isinstance(self._status_suffix, ContextStatus):
            self._events.publish(
                "thinking_start",
                suffix=self._status_suffix.plain(),
                **self._status_suffix.event_data(),
                started_at=self._started_at,
                label=self._label_text,
                progress=self._progress,
                countdown=self._countdown,
                round_elapsed=self._round_elapsed,
            )
        else:
            self._events.publish(
                "thinking_start",
                suffix=self._status_suffix,
                started_at=self._started_at,
                label=self._label_text,
                progress=self._progress,
                countdown=self._countdown,
                round_elapsed=self._round_elapsed,
            )

    def _publish_stop(self) -> None:
        if self._events is not None:
            self._events.publish("thinking_stop")

    def _base_label(self, elapsed: float) -> str:
        if self._countdown is not None:
            remaining = max(0, self._countdown - elapsed)
            return f"{self._label_text}... ({_format_duration(remaining)})"
        if elapsed:
            return f"{self._label_text}... ({elapsed:.0f}s)"
        return f"{self._label_text}..."

    def _prompt_status(
        self, frame: str, elapsed: float
    ) -> list[tuple[str, str]]:
        label = self._base_label(elapsed)
        fragments = [
            ("class:status.spinner", f"{frame} "),
            ("class:status.text", label),
        ]
        width = 14
        if self._progress:
            position = int(elapsed * 8) % (width * 2 - 2)
            position = min(position, width * 2 - 2 - position)
            start = max(0, position - 1)
            pulse = min(3, width - start)
            fragments.extend([
                ("", " "),
                ("class:status.track", "━" * start),
                ("class:status.progress", "━" * pulse),
                ("class:status.track", "━" * (width - start - pulse)),
            ])
        # Context usage is drawn by the prompt itself (right-aligned), so a
        # ContextStatus suffix adds nothing here.
        if self._status_suffix and not isinstance(self._status_suffix, ContextStatus):
            fragments.append(("class:status.detail", f" · {self._status_suffix}"))
        for rate in self._rate_parts():
            fragments.append(("class:status.rate", f" · {rate}"))
        if self._round_elapsed is not None:
            fragments.append((
                "class:status.detail",
                f" · round {_format_round_duration(self._round_elapsed)}",
            ))
        return fragments

    def _rate_parts(self) -> list[str]:
        parts = []
        if self._ttft is not None:
            parts.append(f"TTFT {_format_latency(self._ttft)}")
        if self._tps is not None:
            parts.append(f"{self._tps:.0f} tok/s")
        return parts

    def _label_plain(self, elapsed: float) -> str:
        parts = [self._base_label(elapsed)]
        if self._status_suffix:
            parts.append(
                self._status_suffix.plain()
                if isinstance(self._status_suffix, ContextStatus)
                else str(self._status_suffix)
            )
        parts.extend(self._rate_parts())
        if self._round_elapsed is not None:
            parts.append(f"round {_format_round_duration(self._round_elapsed)}")
        return " · ".join(parts)

    def _label(self, elapsed: float) -> str | Table:
        base = self._base_label(elapsed)
        cells: list = []
        if self._progress:
            cells.append(ProgressBar(total=None, pulse=True, width=14, pulse_style="cyan"))
        if isinstance(self._status_suffix, ContextStatus):
            cells.append(self._status_suffix.render())
        elif self._status_suffix:
            cells.append(Text(str(self._status_suffix), style="dim"))
        cells.extend(Text(rate, style="dim") for rate in self._rate_parts())
        if self._round_elapsed is not None:
            cells.append(Text(
                f"round {_format_round_duration(self._round_elapsed)}", style="dim"
            ))
        if not cells:
            return base
        row = Table.grid(padding=(0, 1))
        separated: list = [Text(base)]
        for index, cell in enumerate(cells):
            # The progress bar sits directly beside the label.
            if index or not self._progress:
                separated.append(Text("·", style="dim"))
            separated.append(cell)
        row.add_row(*separated)
        return row

    def _tick(self) -> None:
        """Background loop that updates the elapsed-time label."""
        while self._running:
            self._stop_event.wait(0.08 if self._status_sink is not None else 0.5)
            if not self._running:
                continue
            elapsed = time.monotonic() - self._start_time
            try:
                if self._indicator_stack is not None:
                    assert self._indicator_lock is not None
                    with self._indicator_lock:
                        if self._indicator_stack[-1] is not self:
                            continue
                        self._update_visual(elapsed)
                else:
                    self._update_visual(elapsed)
            except Exception:
                pass  # best-effort; don't crash the agent on a display glitch

    def _update_visual(self, elapsed: float) -> None:
        if self._status_sink is not None:
            frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
            frame = frames[int(elapsed / 0.08) % len(frames)]
            self._status_sink(self._prompt_status(frame, elapsed))
        elif self._status is not None:
            self._status.update(self._label(elapsed))


class _CompactMarkdown(Markdown):
    """Markdown without Rich's leading blank line for block elements."""

    def __rich_console__(self, console, options):
        started = False
        for segment in super().__rich_console__(console, options):
            if not started and segment.text == "\n":
                continue
            if segment.text:
                started = True
            yield segment


class _TerminalTextStream:
    """Append-only styled text renderer that commits completed lines.

    Prompt Toolkit redraws its active prompt after each stdout flush, so an
    unterminated fragment would be overwritten by the redraw.
    """

    def __init__(self, console: Console, style: str, prefix: str = f"{_BODY}{_GUTTER} "):
        self._console = console
        self._style = style
        self._prefix = prefix
        self._pending = ""

    def _print_line(self, line: str) -> None:
        self._console.print(_Gutter(
            Text(line, style=self._style),
            self._prefix,
            self._prefix,
            style=self._style,
        ))

    def feed(self, text: str) -> None:
        self._pending += text
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            self._print_line(line.removesuffix("\r"))

    def flush(self) -> None:
        if self._pending:
            self._print_line(self._pending)
            self._pending = ""

    def abort(self) -> None:
        self._pending = ""


class _TerminalMarkdownStream:
    """Append-only renderer that commits parser-confirmed Markdown blocks.

    The newest top-level block remains buffered because later lines may still
    extend it (notably lists containing quotes or fenced code). Once another
    top-level block starts, earlier blocks can be printed without rewriting
    terminal scrollback. Paragraphs are also safe to commit at a blank line.
    """

    _BLANK_TERMINATED = {
        "blockquote_open",
        "heading_open",
        "paragraph_open",
        "table_open",
        "thematic_break",
    }
    def __init__(self, console: Console):
        self._console = console
        self._parser = MarkdownIt().enable("strikethrough").enable("table")
        self._pending = ""
        self._buffer = ""
        self._references: dict[str, dict] = {}
        self._started = False
        # Wall-clock time and model shown in the response header.
        self.created_at: float | None = None
        self.model: str | None = None
        # Re-parsing the whole uncommitted block on every line is quadratic
        # for a long list, table or fence. After a parse that commits nothing,
        # wait for a structural hint or geometric growth before trying again.
        self._last_block_type: str | None = None
        self._next_parse_len = 0

    def _print(self, renderable) -> None:
        if not self._started:
            self._console.print(_response_header(self.created_at, self.model))
            self._started = True
        self._console.print(_Gutter(renderable, _BODY))

    def _parse(self, source: str):
        env: dict = {}
        tokens = self._parser.parse(source, env)
        blocks = [
            (token.map[0], token.map[1], token.type)
            for token in tokens
            if token.level == 0 and token.map is not None
        ]

        # Definitions don't normally emit tokens. Treat standalone definitions
        # as blocks so they don't get detached from the text that may use them.
        for reference in env.get("references", {}).values():
            start, end = reference["map"]
            if not any(a <= start and end <= b for a, b, _ in blocks):
                blocks.append((start, end, "definition"))
        blocks.sort(key=lambda block: (block[0], block[1]))
        return tokens, env, blocks

    @staticmethod
    def _line_offset(source: str, line_number: int) -> int:
        offset = 0
        for _ in range(line_number):
            offset = source.index("\n", offset) + 1
        return offset

    def _unresolved_block_start(
        self,
        source: str,
        blocks: list[tuple[int, int, str]],
        references: dict[str, dict],
    ) -> int | None:
        known = set(self._references) | set(references)
        for match in _REFERENCE_LINK_RE.finditer(source):
            label = match.group(2) or match.group(1)
            if normalizeReference(label) in known:
                continue
            line = source.count("\n", 0, match.start())
            for start, end, _ in blocks:
                if start <= line < end:
                    return start
            return line
        return None

    @staticmethod
    def _fence_is_closed(lines: list[str], end: int, markup: str) -> bool:
        if not markup or end < 2:
            return False
        line = lines[end - 1]
        indent = len(line) - len(line.lstrip(" "))
        if indent > 3:
            return False
        closing = line[indent:].rstrip(" \t")
        return (
            len(closing) >= len(markup)
            and not closing.strip(markup[0])
        )

    def _stable_line(self, source: str, env: dict, blocks, tokens) -> int:
        if not blocks:
            return 0

        # Everything before the newest top-level block is structurally final.
        stable = blocks[-1][0] if len(blocks) > 1 else 0

        # A blank line conclusively ends these block types. Lists and indented
        # code are intentionally excluded because they can resume after blanks.
        _, last_end, last_type = blocks[-1]
        lines = source.splitlines()
        ended_at_blank = (
            lines
            and not lines[-1].strip()
            and last_type in self._BLANK_TERMINATED
            and last_end < len(lines)
        )
        last_token = next(
            (
                token
                for token in reversed(tokens)
                if token.level == 0 and token.map is not None
            ),
            None,
        )
        closed_fence = (
            last_type == "fence"
            and last_token is not None
            and last_token.type == "fence"
            and self._fence_is_closed(lines, last_end, last_token.markup)
        )
        if ended_at_blank or closed_fence:
            stable = last_end

        unresolved = self._unresolved_block_start(
            source, blocks, env.get("references", {})
        )
        if unresolved is not None:
            stable = min(stable, unresolved)
        return stable

    def _render(self, source: str, references: dict[str, dict] | None = None) -> None:
        env = {"references": dict(self._references)}
        if references:
            env["references"].update(references)
        tokens = self._parser.parse(source, env)
        if not tokens:
            return
        markdown = _CompactMarkdown(source)
        markdown.parsed = tokens
        self._print(markdown)

    # Buffers shorter than this are always re-parsed: it is cheap, and it
    # keeps short replies rendering promptly at every block boundary.
    _EAGER_PARSE_CHARS = 4096

    def _should_parse(self, new_lines: str) -> bool:
        if len(self._buffer) < self._EAGER_PARSE_CHARS:
            return True
        if len(self._buffer) >= self._next_parse_len:
            return True
        block_type = self._last_block_type
        if block_type == "fence":
            # Only a fence marker can close a fenced block.
            return any(
                line.lstrip(" ").startswith(("```", "~~~"))
                for line in new_lines.splitlines()
            )
        if block_type in self._BLANK_TERMINATED:
            return any(not line.strip() for line in new_lines.splitlines())
        return False

    def _commit_stable(self) -> None:
        tokens, env, blocks = self._parse(self._buffer)
        self._last_block_type = blocks[-1][2] if blocks else None
        stable_line = self._stable_line(self._buffer, env, blocks, tokens)
        if not stable_line:
            self._next_parse_len = len(self._buffer) + len(self._buffer) // 4
            return
        self._next_parse_len = 0
        offset = self._line_offset(self._buffer, stable_line)
        source, self._buffer = self._buffer[:offset], self._buffer[offset:]
        self._render(source, env.get("references"))
        for label, reference in env.get("references", {}).items():
            if reference["map"][1] <= stable_line:
                self._references[label] = reference

    def feed(self, text: str) -> None:
        # Chunk boundaries carry no Markdown meaning. Preserve the provider's
        # text exactly and wait for real newlines before parsing complete lines.
        self._pending += text
        complete = self._pending.rpartition("\n")
        if not complete[1]:
            return
        new_lines = complete[0] + "\n"
        self._buffer += new_lines
        self._pending = complete[2]
        if self._should_parse(new_lines):
            self._commit_stable()

    def close(self) -> None:
        self._buffer += self._pending
        self._pending = ""
        if self._buffer:
            self._render(self._buffer)
            self._buffer = ""

    def abort(self) -> None:
        self._pending = ""
        self._buffer = ""
        self._next_parse_len = 0


class ResponseStream:
    """Append-only terminal sink for a streamed assistant turn.

    Complete Markdown blocks are rendered as they arrive. The active top-level
    container remains buffered so nested lists, quotes, tables, and fenced code
    retain their structure. No committed terminal rows are rewritten, so tall
    responses remain correct in scrollback. Web clients receive coalesced
    token fragments plus consolidated events for late joiners.
    """

    # Token fragments are batched for web clients: one event per token would
    # cost a frame and a render per token and evict real history from the
    # bounded reconnect buffer within a single long reply.
    DELTA_INTERVAL_SECONDS = 0.05
    DELTA_MAX_CHARS = 2048

    def __init__(
        self,
        console: Console,
        events: "EventHub | None",
        *,
        show_thinking: bool = False,
        render_terminal: bool = True,
        model: str | None = None,
    ):
        self._console = console
        self._events = events
        self._show_thinking = show_thinking
        self.model = model
        # A quiet console discards output only after Rich has fully rendered
        # it, so skip the (incremental Markdown) rendering work altogether.
        self._render_terminal = render_terminal
        self._content_parts: list[str] = []
        self._thinking_parts: list[str] = []
        self._content_visible = False
        self._thinking_visible = False
        self._thinking_terminal = _TerminalTextStream(console, "thinking")
        self._terminal = _TerminalMarkdownStream(console)
        self._closed = False
        self._lock = threading.Lock()
        self._delta_type: str | None = None
        self._delta_parts: list[str] = []
        self._delta_chars = 0
        self._delta_published_at = 0.0
        self._delta_timer: threading.Timer | None = None
        self.created_at: float | None = None

    def __enter__(self) -> "ResponseStream":
        return self

    def __exit__(self, exc_type, *args) -> None:
        self.close(render_terminal=exc_type is None)

    def _flush_delta_locked(self) -> None:
        if self._delta_timer is not None:
            self._delta_timer.cancel()
            self._delta_timer = None
        if self._delta_parts and self._events is not None:
            data = {"text": "".join(self._delta_parts)}
            if self._delta_type == "assistant_delta" and self.model:
                data["model"] = self.model
            self._events.publish(self._delta_type, **data)
            self._delta_published_at = time.monotonic()
        self._delta_type = None
        self._delta_parts = []
        self._delta_chars = 0

    def _flush_delta_later(self) -> None:
        with self._lock:
            self._delta_timer = None
            if not self._closed:
                self._flush_delta_locked()

    def _queue_delta_locked(self, kind: str, text: str) -> None:
        if self._events is None:
            return
        if self._delta_type != kind:
            self._flush_delta_locked()
            self._delta_type = kind
        self._delta_parts.append(text)
        self._delta_chars += len(text)
        wait = self.DELTA_INTERVAL_SECONDS - (time.monotonic() - self._delta_published_at)
        if wait <= 0 or self._delta_chars >= self.DELTA_MAX_CHARS:
            self._flush_delta_locked()
        elif self._delta_timer is None:
            # Publish the tail even if the provider goes quiet (for example
            # while it streams a large tool call after the visible text).
            self._delta_timer = threading.Timer(wait, self._flush_delta_later)
            self._delta_timer.daemon = True
            self._delta_timer.start()

    def on_content(
        self, text: str, *, at: float | None = None, model: str | None = None
    ) -> None:
        """Feed reply text. *at* and *model* override the time and model the
        reply header shows when this fragment is the first visible one
        (relayed history)."""
        with self._lock:
            if self._closed:
                return
            self._content_parts.append(text)
            if not self._content_visible:
                if not text.strip():
                    return
                self._content_visible = True
                self.created_at = time.time() if at is None else at
                self.model = model or self.model
                self._terminal.created_at = self.created_at
                self._terminal.model = self.model
                text = "".join(self._content_parts)
            if self._thinking_visible:
                if self._render_terminal:
                    self._thinking_terminal.flush()
                self._thinking_visible = False
            if self._render_terminal:
                self._terminal.feed(text)
            self._queue_delta_locked("assistant_delta", text)

    def on_thinking(self, text: str) -> None:
        # show_thinking gates reasoning output uniformly across terminal and
        # web, so the two surfaces never disagree on whether a turn had a
        # thinking block.
        if not self._show_thinking:
            return
        with self._lock:
            if self._closed:
                return
            self._thinking_parts.append(text)
            if self._render_terminal:
                self._thinking_terminal.feed(text)
            self._thinking_visible = True
            self._queue_delta_locked("thinking_delta", text)

    def close(self, *, render_terminal: bool = True) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._flush_delta_locked()
            thinking = "".join(self._thinking_parts)
            content = "".join(self._content_parts) if self._content_visible else ""
        if render_terminal and self._render_terminal:
            self._thinking_terminal.flush()
            if content:
                self._terminal.close()
        else:
            self._thinking_terminal.abort()
            self._terminal.abort()
        if self._events is not None:
            if thinking:
                self._events.publish("thinking", text=thinking)
            if content:
                self._events.publish(
                    "assistant_message",
                    text=content,
                    created_at=self.created_at,
                    model=self.model,
                )


def format_round_summary(record: dict) -> str:
    """One-line summary of a ``round_stats`` record, or "" for a round with
    no model call (a command, or a failure before any request)."""

    def number(key: str) -> float:
        value = record.get(key)
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0

    if not number("model_calls"):
        return ""
    cancelled = bool(record.get("cancelled"))
    parts = [f"{'■ stopped' if cancelled else _CHECK} {_format_duration(number('duration'))}"]
    tools = int(number("tool_calls"))
    if tools:
        failures = int(number("tool_failures"))
        label = f"{tools} tool{'s' if tools != 1 else ''}"
        parts.append(f"{label} ({failures} failed)" if failures else label)
    if isinstance(record.get("ttft"), (int, float)):
        parts.append(f"TTFT {_format_latency(float(record['ttft']))}")
    if isinstance(record.get("tps"), (int, float)):
        parts.append(f"{float(record['tps']):.0f} tok/s")
    input_tokens = int(number("input_tokens"))
    parts.append(f"↑{_compact_tokens(input_tokens)} ↓{_compact_tokens(int(number('output_tokens')))}")
    if input_tokens > 0:
        parts.append(f"{number('cached_tokens') / input_tokens:.0%} cached")
    return " · ".join(parts)


class AgentConsole:
    """Thin wrapper around ``rich.Console`` with typed convenience methods."""

    def __init__(self, events: EventHub | None = None, *, render_terminal: bool = True):
        self._console = Console(theme=AGENT_THEME)
        self.render_terminal = render_terminal
        self._console.quiet = not render_terminal
        self.events = events
        self.prompt_broker: PromptBroker | None = None
        self.interactive_input = False
        self.status_sink: (
            Callable[[list[tuple[str, str]] | None], None] | None
        ) = None
        self.context_status_sink: Callable[[ContextStatus], None] | None = None
        self.timeline_reset_sink: Callable[[], None] | None = None
        self._indicator_stack: list[ThinkingIndicator] = []
        self._indicator_lock = threading.RLock()
        self._round_timer_state = threading.local()
        self._quiet_depth = 0
        self._visible_depth = 0

    @property
    def width(self) -> int:
        """Current terminal rendering width."""
        return self._console.width

    @property
    def quiet(self) -> bool:
        """Whether output is currently suppressed (see :meth:`suppressed`)."""
        return self._quiet_depth > 0 and self._visible_depth == 0

    @contextmanager
    def suppressed(self) -> Iterator[None]:
        """Silence rendered output and published events for nested work.

        A context-isolated turn is not part of the conversation: its messages
        are discarded, so rendering them contradicts the caller's summary, and
        publishing them floods the bounded event history until the real
        timeline is evicted from reconnect replay.

        Anything wrapped in :meth:`visible` is exempt, because suppressing it
        would strand the user: interactive prompts (an unanswerable question
        blocks the run) and errors are the only account of *why* the nested
        work failed.
        """
        self._quiet_depth += 1
        self._console.quiet = True
        try:
            yield
        finally:
            self._quiet_depth -= 1
            if self._quiet_depth == 0:
                self._console.quiet = not self.render_terminal

    @contextmanager
    def visible(self) -> Iterator[None]:
        """Render and publish the enclosed output even while :meth:`suppressed`.

        Use it for anything the user must see to stay in control of a nested
        run: a question, a failure, or a refused action. Web clients need these
        as much as the terminal does, so the exemption covers events too.
        """
        self._visible_depth += 1
        was_quiet = self._console.quiet
        self._console.quiet = False
        try:
            yield
        finally:
            self._visible_depth -= 1
            self._console.quiet = was_quiet

    def _emit(self, event_type: str, **data) -> None:
        if self.events is not None and not self.quiet:
            self.events.publish(event_type, **data)

    def _render_plain(self, *objects, markup: bool = True, width: int = 120) -> str:
        """Render rich objects/markup to plain text for web clients."""
        capture_console = Console(width=width, theme=AGENT_THEME, no_color=True)
        with capture_console.capture() as capture:
            capture_console.print(*objects, markup=markup)
        return capture.get().rstrip("\n")

    def _render_ansi(self, *objects, markup: bool = True, width: int = 120) -> str:
        """Render rich objects/markup with ANSI styles for attached terminals."""
        capture_console = Console(
            width=width, theme=AGENT_THEME, force_terminal=True,
            color_system="standard",
        )
        with capture_console.capture() as capture:
            capture_console.print(*objects, markup=markup)
        return capture.get().rstrip("\n")

    @contextmanager
    def round_timer(self) -> Iterator[None]:
        """Track one autonomous round for frozen status-bar totals."""
        stack = getattr(self._round_timer_state, "stack", None)
        if stack is None:
            stack = []
            self._round_timer_state.stack = stack
        stack.append(time.monotonic())
        try:
            yield
        finally:
            stack.pop()

    def _round_elapsed(self) -> float | None:
        stack = getattr(self._round_timer_state, "stack", None)
        if not stack:
            return None
        return time.monotonic() - stack[-1]

    def thinking(
        self,
        *,
        status_suffix: str | ContextStatus = "",
        label: str = "Working",
        progress: bool = False,
        countdown: float | None = None,
        initial_elapsed: float = 0.0,
        round_elapsed: float | None = None,
    ) -> ThinkingIndicator:
        """Return a context manager that displays an animated thinking indicator.

        ``status_suffix`` is appended after the elapsed-time counter. Set
        ``progress`` for an indeterminate bar, or ``countdown`` for a decreasing
        duration. A :class:`ContextStatus` renders context usage as a progress
        bar in the terminal and plain text on web.

        Usage::

            with console.thinking():
                response = client.chat.completions.create(...)
        """
        if self.quiet:
            # Inert indicator: taking over the display would replace the
            # enclosing indicator's progress with an item's transient status.
            return ThinkingIndicator(
                self._console,
                None,
                status_suffix=status_suffix,
                label=label,
                round_elapsed=self._round_elapsed() if round_elapsed is None else round_elapsed,
                initial_elapsed=initial_elapsed,
                render_terminal=False,
            )
        if isinstance(status_suffix, ContextStatus) and self.context_status_sink is not None:
            self.context_status_sink(status_suffix)
        return ThinkingIndicator(
            self._console,
            self.events,
            status_suffix=status_suffix,
            label=label,
            progress=progress,
            countdown=countdown,
            round_elapsed=self._round_elapsed() if round_elapsed is None else round_elapsed,
            initial_elapsed=initial_elapsed,
            render_terminal=self.render_terminal and not self.interactive_input,
            status_sink=self.status_sink if self.interactive_input else None,
            indicator_stack=self._indicator_stack,
            indicator_lock=self._indicator_lock,
        )

    def stream_response(
        self, *, show_thinking: bool = False, model: str | None = None
    ) -> ResponseStream:
        """Return an append-only terminal sink for a streamed assistant turn.

        Usage::

            with console.stream_response() as sink:
                message, usage = consume_stream(stream, on_content=sink.on_content, ...)
        """
        return ResponseStream(
            self._console,
            self.events if not self.quiet else None,
            show_thinking=show_thinking,
            render_terminal=not self._console.quiet,
            model=model,
        )

    # -- raw pass-through (for rich markup, tables, etc.) -------------------

    # print() accepts ``wide=`` (see below).
    supports_wide_output = True

    def print(self, *args, wide: str | None = None, **kwargs):
        """Print rich content.

        *wide* is an alternative rendering of the same content for clients
        that wrap text themselves (the Web UI): rows clipped to fit a
        terminal can carry more there. Attached terminals keep the narrow form.
        """
        if self.render_terminal:
            self._console.print(*args, **kwargs)
        if args and self.events is not None:
            markup = kwargs.get("markup", True)
            data = {
                "text": self._render_plain(*args, markup=markup),
                "ansi": self._render_ansi(*args, markup=markup),
            }
            if wide is not None:
                data["wide_text"] = self._render_plain(wide, markup=markup, width=WIDE_RENDER_WIDTH)
                data["wide_ansi"] = self._render_ansi(wide, markup=markup, width=WIDE_RENDER_WIDTH)
            self._emit("output", **data)

    def local(self, *args, **kwargs):
        """Print terminal-only information such as authentication secrets.

        Renders even under :meth:`suppressed`: interactive questions may be
        terminal-only, and an invisible question cannot be answered.
        """
        with self.visible():
            self._console.print(*args, **kwargs)

    def table(self, table: Table):
        if self.render_terminal:
            self._console.print(table)
        if self.events is not None:
            self._emit("output", text=self._render_plain(table))

    def stream_output(self, text: str, *, dim: bool = True) -> None:
        """Write a plain-text block straight to the terminal file, bypassing
        rich's per-line layout cost.

        Used for high-volume command output: rich renders roughly 14k lines/s,
        which would throttle the child process through pipe backpressure. Raw
        block writes are orders of magnitude faster, and command output is
        data, so markup and ANSI escapes pass through untouched. Web clients
        still receive the same ``output`` event as :meth:`print`.
        """
        if not text or self.quiet:
            return
        console = self._console
        if self.render_terminal:
            if dim and console.is_terminal and console.color_system not in (None, "windows"):
                console.file.write(f"\x1b[2m{text}\x1b[22m")
            else:
                console.file.write(text)
            if not text.endswith("\n"):
                console.file.write("\n")
            console.file.flush()
        if self.events is not None:
            self._emit("output", text=text.rstrip("\n"))

    def _agent_panel(
        self, rows: list[tuple[str, str]], *, show_logo: bool = True
    ) -> None:
        """Render an agent information panel with the kiwi theme."""
        details = Table.grid(padding=(0, 1), expand=True)
        details.add_column(width=12, no_wrap=True, style="dim blue")
        details.add_column(ratio=1, style="white", overflow="fold")
        for label, value in rows:
            details.add_row(label, Text(value, style="bold"))

        if show_logo:
            logo = Text(_AGENT_LOGO.rstrip("\n"), style="bold blue")
            body = Table.grid(expand=True)
            logo_width = max(len(line) for line in _AGENT_LOGO.splitlines())
            body.add_column(width=logo_width + 4, no_wrap=True)
            body.add_column(ratio=1)
            body.add_row(logo, details)
        else:
            body = details
        self.print(Panel(body, border_style="blue", padding=(0, 2)))

    def startup_panel(
        self,
        model: str,
        context: str,
        reasoning: str,
        persona: str,
        skills: str,
        workspace: str,
        peers: str = "",
    ) -> None:
        """Render the interactive agent's startup summary."""
        rows = [
            ("Model", f"{model} ({context}) · {reasoning}"),
            ("Persona", persona),
            ("Skills", skills),
            ("Workspace", workspace),
        ]
        if peers:
            rows.append(("Peers", peers))
        self._agent_panel(rows)

    def session_end_panel(
        self,
        *,
        total: int,
        prompt: int,
        cached_prompt: int,
        completion: int,
        reasoning: int,
        resume: str,
    ) -> None:
        """Render token usage and the command for resuming a session."""
        usage = (
            f"{total:,} total · {prompt:,} input · {cached_prompt:,} cached input · "
            f"{completion:,} output · {reasoning:,} reasoning"
        )
        self._agent_panel([
            ("Tokens", usage),
            ("Resume", f"ene --resume {resume}"),
        ], show_logo=False)

    def rule(self):
        if self.render_terminal:
            self._console.rule(style="dim color(240)")
        self._emit("rule")

    def reset_timeline(self):
        """Clear rendered history before replaying authoritative context."""
        if self.render_terminal and self._console.is_terminal:
            if self.timeline_reset_sink is not None:
                self.timeline_reset_sink()
            else:
                print("\033[3J\033[2J\033[H", end="", file=self._console.file, flush=True)
        self._emit("timeline_reset")

    # -- block helper -------------------------------------------------------

    def _block(self, msg: str, style: str, *, icon: str = _DOT):
        """Print an indented block: *icon* on the first line, continuation
        (and wrapped) lines aligned after it."""
        if not self.render_terminal:
            return
        self._console.print(_Gutter(Text(msg, style=style), f"{_BODY}{icon} ", style=style))

    # -- typed log helpers --------------------------------------------------

    def system(self, msg: str):
        self._block(msg, style="system")
        self._emit("system", text=msg)

    def debug(self, msg: str):
        self._block(msg, style="debug")
        self._emit("debug", text=msg)

    def error(self, msg: str):
        # Errors survive suppression: a nested turn that failed on an API error
        # reports only "no response" to its caller, so this is the sole account
        # of what actually went wrong.
        with self.visible():
            self._block(msg, style="error", icon=_CROSS)
            self._emit("error", text=msg)

    def warn(self, msg: str, *, exc_info: bool = False):
        self._block(msg, style="warning", icon=_WARN)
        self._emit("warning", text=msg)
        if exc_info:
            self._console.print_exception()

    def tool(
        self,
        msg: str,
        *,
        name: str | None = None,
        primary: str = "",
        qualifiers: list[str] | None = None,
    ):
        """Render a tool call, using semantic spans when structured data is available."""
        qualifiers = qualifiers or []
        if name is None:
            self._block(msg, style="tool")
            self._emit("tool_start", text=msg)
            return

        line = Text(name, style="tool_name")
        if primary:
            line.append(f" {primary}", style="tool_primary")
        for qualifier in qualifiers:
            line.append(f" · {qualifier}", style="tool_meta")
        if self.render_terminal:
            self._console.print(_Gutter(line, f"{_BODY}{_DOT} ", style="tool"))
        self._emit(
            "tool_start",
            text=msg,
            name=name,
            primary=primary,
            qualifiers=qualifiers,
        )

    def tool_result(self, msg: str, success: bool = True, image: dict[str, str] | None = None):
        style = "tool_ok" if success else "tool_fail"
        icon = _CHECK if success else _CROSS
        if self.render_terminal:
            self._console.print(_Gutter(
                Text(msg, style=style), f"{_BODY}  {_BRANCH} {icon} ", style=style
            ))
        # Web clients render *image* (a local path and media type) as a
        # preview; the bytes themselves stay out of the event stream.
        if image:
            self._emit("tool_result", text=msg, success=success, image=image)
        else:
            self._emit("tool_result", text=msg, success=success)

    def _highlight_line(self, code: str, language: str) -> "Text":
        """Return a ``rich.text.Text`` with syntax-highlighted *code*.

        Falls back to plain white text when highlighting isn't available.
        """
        from rich.syntax import Syntax
        from rich.text import Text as RichText

        try:
            syntax = Syntax(
                code, language, theme="monokai",
                line_numbers=False, background_color=None,
            )
            segments = list(self._console.render(syntax))
            # Syntax renderer may append a trailing newline; drop it.
            if segments and segments[-1].text == "\n":
                segments.pop()
            text = RichText()
            for segment in segments:
                text.append(segment.text, style=segment.style)
            return text
        except Exception:
            return RichText(code, style="white")

    def _guess_language(self, path: str) -> str:
        """Guess a Pygments lexer name from a file extension."""
        ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        _MAP = {
            "py": "python", "pyi": "python", "pyx": "python",
            "js": "javascript", "ts": "typescript", "tsx": "typescript",
            "jsx": "javascript", "json": "json", "yaml": "yaml", "yml": "yaml",
            "toml": "toml", "cfg": "ini", "ini": "ini",
            "html": "html", "css": "css", "scss": "scss",
            "md": "markdown", "rst": "rst",
            "sh": "bash", "bash": "bash", "zsh": "bash",
            "ps1": "powershell", "psm1": "powershell",
            "sql": "sql", "rs": "rust", "go": "go",
            "java": "java", "kt": "kotlin", "swift": "swift",
            "c": "c", "h": "c", "cpp": "cpp", "hpp": "cpp",
            "rb": "ruby", "php": "php", "lua": "lua",
            "vim": "vim", "dockerfile": "dockerfile",
        }
        return _MAP.get(ext, "text")

    def _render_diff_block(
        self,
        lines: list[str],
        sign: str,
        sign_style: str,
        bg_color: str,
        indent: str,
        pad_to: int,
        language: str,
        start_line: int,
        limit: int | None = None,
    ):
        """Render a block of diff lines (either removals or additions).

        When *limit* is set, only the first *limit* lines are rendered,
        and the count of omitted lines is returned.
        """
        from rich.text import Text as RichText

        LN = "color(245)"
        rendered = 0
        for i, line in enumerate(lines):
            if limit is not None and rendered >= limit:
                break
            ln = f"{start_line + i:>4} "
            t = RichText(f"{indent}", style="")
            t.append(ln, style=LN)
            t.append(f"{sign} ", style=sign_style)
            t.append(self._highlight_line(line, language))
            padding = max(0, pad_to - t.cell_len)
            if padding:
                t.append(" " * padding)
            t.stylize(f"on {bg_color}")
            self._console.print(t)
            rendered += 1
        return len(lines) - rendered  # omitted count

    def diff_edit(
        self,
        path: str,
        old_text: str | None,
        new_text: str | None,
        line_num: int | None = None,
        count: int = 1,
        success: bool = True,
    ):
        """Render a file diff with +/- markers, muted backgrounds, and
        syntax highlighting.

        - Dark red background for removed lines (prefixed ``-``)
        - Dark green background for added lines (prefixed ``+``)
        - Full terminal-width backgrounds so the diff stands out as a block.
        - When *old_text* is empty or ``None`` (e.g. write_file), only ``+`` lines are shown.
        - Large diffs (> DIFF_MAX_LINES total) show a summary with preview instead of
          the full diff to avoid flooding the terminal.
        """
        # Muted, dark backgrounds — visible but not jarring
        BG_RED = "#3a1a1a"
        BG_GREEN = "#1a3a1a"
        SIGN_RED = "#f87171"
        SIGN_GREEN = "#4ade80"

        icon = _CHECK if success else _CROSS
        style = "tool_ok" if success else "tool_fail"

        # Build header line
        if count > 1:
            header = f"{path}  ({count} occurrences)"
        elif line_num is not None:
            header = f"{path}  line {line_num}"
        else:
            header = f"{path}"

        prefix = f"{_BODY}  {_BRANCH} {icon} "
        self._console.print(f"{prefix}{header}", style=style)
        self._emit(
            "diff",
            path=path,
            old_text=old_text or "",
            new_text=new_text or "",
            line_num=line_num,
            count=count,
            success=success,
        )

        if not success:
            return

        indent = " " * len(prefix)
        pad_to = min(self._console.width, 160)
        language = self._guess_language(path)
        has_old = bool(old_text)
        start_line = line_num if line_num is not None else 1

        old_lines = old_text.splitlines() if old_text else []
        new_lines = new_text.splitlines() if new_text else []
        total_lines = len(old_lines) + len(new_lines)

        # ── summary mode for large diffs ──
        if total_lines > DIFF_MAX_LINES:
            removed = len(old_lines)
            added = len(new_lines)
            parts = []
            if removed:
                parts.append(f"{removed} removed")
            if added:
                parts.append(f"{added} added")
            stat = ", ".join(parts)
            self._console.print(
                f"{indent}{stat}  (diff too large — showing first {DIFF_PREVIEW_LINES} lines per side)",
                style="dim",
            )

            preview = DIFF_PREVIEW_LINES
            if has_old:
                omitted = self._render_diff_block(
                    old_lines, "-", f"bold {SIGN_RED}", BG_RED,
                    indent, pad_to, language, start_line, limit=preview,
                )
                if omitted:
                    self._console.print(
                        f"{indent}...  ({omitted} more removed lines)", style="dim"
                    )
            if new_lines:
                omitted = self._render_diff_block(
                    new_lines, "+", f"bold {SIGN_GREEN}", BG_GREEN,
                    indent, pad_to, language, start_line, limit=preview,
                )
                if omitted:
                    self._console.print(
                        f"{indent}...  ({omitted} more added lines)", style="dim"
                    )
            return

        # ── full diff ──
        if has_old:
            self._render_diff_block(
                old_lines, "-", f"bold {SIGN_RED}", BG_RED,
                indent, pad_to, language, start_line,
            )

        if new_lines:
            self._render_diff_block(
                new_lines, "+", f"bold {SIGN_GREEN}", BG_GREEN,
                indent, pad_to, language, start_line,
            )

    def round_summary(self, record: dict) -> None:
        """Print one dim line closing a round (terminal only; web clients
        render the ``round_stats`` event themselves)."""
        if not self.render_terminal or self.quiet:
            return
        line = format_round_summary(record)
        if line:
            self._console.print(_Gutter(Text(line, style="round_summary"), _BODY))

    def thinking_message(self, msg: str):
        """Render a completed reasoning block outside the streaming path."""
        stream = _TerminalTextStream(self._console, "thinking")
        stream.feed(msg)
        stream.flush()
        self._emit("thinking", text=msg)

    def response(
        self,
        msg: str,
        *,
        created_at: float | None | object = _NOW,
        model: str | None = None,
    ):
        """Print a complete reply under a 💠 header naming *model*.

        *created_at* stamps the header; the default is now, and ``None`` (a
        message saved before timestamps existed) omits the time.
        """
        stamp = _stamp(created_at)
        if self.render_terminal:
            _print_markdown_response(self._console, msg, stamp, model)
        self._emit("assistant_message", text=msg, created_at=stamp, model=model)

    def user_input(
        self,
        msg: str,
        *,
        source: str = "replay",
        submission_id: str | None = None,
        steer: bool = False,
        with_rule: bool = True,
        created_at: float | None | object = _NOW,
    ):
        """Print user input under a header with its time (see :meth:`response`).

        Single emission point for ``user_message`` events — callers must not
        publish the event themselves.
        """
        if with_rule:
            self._console.rule(style="dim color(240)")
        if not msg.splitlines():
            return
        stamp = _stamp(created_at)
        if self.render_terminal:
            if source == "inbox":
                header = _header(_INBOX, "Notifications", "system", stamp)
            else:
                header = _header(_USER, "You", "input", stamp, *(("steer",) if steer else ()))
            self._console.print(header)
            self._console.print(_Gutter(Text(msg, style="input"), _BODY))
        data = {"text": msg, "source": source, "created_at": stamp}
        if submission_id is not None:
            data["submission_id"] = submission_id
        if steer:
            data["steer"] = True
        self._emit("user_message", **data)

    # -- interactive prompts ------------------------------------------------

    def select(
        self,
        message: str,
        choices: list[str],
        *,
        default: str | None = None,
        use_indicator: bool = True,
    ) -> str | None:
        """Present a scrollable single-select list via questionary.

        Returns the chosen string, or None if cancelled.
        """
        if self.prompt_broker is not None:
            return self.prompt_broker.ask(
                "select", message, choices=choices, default=default or ""
            )
        return self.select_terminal(
            message,
            choices,
            default=default,
            use_indicator=use_indicator,
        )

    def select_terminal(
        self,
        message: str,
        choices: list[str],
        *,
        default: str | None = None,
        use_indicator: bool = True,
    ) -> str | None:
        """Render a select prompt only on the local terminal."""
        try:
            return questionary.select(
                message,
                choices=choices,
                default=default,
                style=_QS_STYLE,
                use_indicator=use_indicator,
            ).unsafe_ask()
        except (KeyboardInterrupt, EOFError):
            return None

    def checkbox_terminal(
        self,
        message: str,
        choices: list[str],
    ) -> list[str] | None:
        """Render a multi-select prompt only on the local terminal."""
        try:
            return questionary.checkbox(
                message,
                choices=choices,
                style=_QS_STYLE,
            ).unsafe_ask()
        except (KeyboardInterrupt, EOFError):
            return None

    async def select_terminal_async(
        self,
        message: str,
        choices: list[str],
        *,
        default: str | None = None,
        use_indicator: bool = True,
    ) -> str | None:
        """Render a local select while allowing web input to race it."""
        question = questionary.select(
            message,
            choices=choices,
            default=default,
            style=_QS_STYLE,
            use_indicator=use_indicator,
        )
        try:
            return await question.unsafe_ask_async()
        except (KeyboardInterrupt, EOFError):
            return None

    def ask_text(
        self,
        message: str,
        *,
        default: str = "",
        multiline: bool = False,
        validate: questionary.Validator | None = None,
    ) -> str | None:
        """Prompt for free-text input via questionary.

        Returns the input string, or None if cancelled.
        """
        if self.prompt_broker is not None:
            return self.prompt_broker.ask(
                "text", message, default=default
            )
        return self.ask_text_terminal(
            message, default=default, multiline=multiline, validate=validate
        )

    def ask_text_terminal(
        self,
        message: str,
        *,
        default: str = "",
        multiline: bool = False,
        validate: questionary.Validator | None = None,
    ) -> str | None:
        """Render a text prompt only on the local terminal."""
        try:
            return questionary.text(
                message,
                default=default,
                multiline=multiline,
                validate=validate,
                style=_QS_STYLE,
            ).unsafe_ask()
        except (KeyboardInterrupt, EOFError):
            return None

    async def ask_text_terminal_async(
        self,
        message: str,
        *,
        default: str = "",
        multiline: bool = False,
        validate: questionary.Validator | None = None,
    ) -> str | None:
        """Render a local text prompt while allowing web input to race it."""
        question = questionary.text(
            message,
            default=default,
            multiline=multiline,
            validate=validate,
            style=_QS_STYLE,
        )
        try:
            return await question.unsafe_ask_async()
        except (KeyboardInterrupt, EOFError):
            return None
