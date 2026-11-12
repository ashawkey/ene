import io
import time
from types import SimpleNamespace

import pytest
from rich.console import Console

from ene.terminal import TerminalInput
from ene.ui import AgentConsole, ContextStatus, ResponseStream, ThinkingIndicator


def test_checkbox_terminal_returns_selected_choices(monkeypatch):
    class Question:
        def unsafe_ask(self):
            return ["one", "two"]

    seen = []
    monkeypatch.setattr(
        "ene.ui.questionary.checkbox",
        lambda message, **kwargs: seen.append((message, kwargs)) or Question(),
    )

    assert AgentConsole().checkbox_terminal("Kill sessions", ["one", "two"]) == [
        "one", "two",
    ]
    assert seen[0][0] == "Kill sessions"
    assert seen[0][1]["choices"] == ["one", "two"]


def test_checkbox_terminal_normalizes_cancellation(monkeypatch):
    class Question:
        def unsafe_ask(self):
            raise EOFError

    monkeypatch.setattr("ene.ui.questionary.checkbox", lambda *args, **kwargs: Question())

    assert AgentConsole().checkbox_terminal("Kill sessions", ["one"]) is None


@pytest.mark.parametrize("error", [KeyboardInterrupt, EOFError])
@pytest.mark.parametrize("kind", ["text", "select"])
def test_terminal_prompts_treat_ctrl_d_as_cancellation(monkeypatch, kind, error):
    class Question:
        def unsafe_ask(self):
            raise error

    monkeypatch.setattr(f"ene.ui.questionary.{kind}", lambda *args, **kwargs: Question())
    console = AgentConsole()

    if kind == "text":
        assert console.ask_text_terminal("Answer") is None
    else:
        assert console.select_terminal("Pick", ["a"]) is None


def test_terminal_title_animates_working_and_marks_done(monkeypatch):
    from prompt_toolkit.output import DummyOutput
    from prompt_toolkit.application.current import create_app_session

    output = DummyOutput()
    titles = []
    monkeypatch.setattr(output, "set_title", titles.append)
    monkeypatch.setattr(output, "clear_title", lambda: titles.append(None))
    monkeypatch.setattr(TerminalInput, "TITLE_ANIMATION_INTERVAL", 0.01)

    with create_app_session(output=output):
        terminal = TerminalInput(work_dir="/tmp/project", title_name="my session")
    assert titles[-1] == "✓ ene my session"

    terminal.set_busy(True)
    deadline = time.monotonic() + 1
    while "◑ ene my session" not in titles and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "◐ ene my session" in titles
    assert "◑ ene my session" in titles

    terminal.set_busy(False)
    assert titles[-1] == "✓ ene my session"
    terminal.close_title()
    assert titles[-1] is None


def test_terminal_title_sanitizes_name_and_uses_workspace_fallback(monkeypatch):
    from prompt_toolkit.output import DummyOutput
    from prompt_toolkit.application.current import create_app_session

    output = DummyOutput()
    titles = []
    monkeypatch.setattr(output, "set_title", titles.append)

    with create_app_session(output=output):
        terminal = TerminalInput(work_dir="/tmp/project")
    assert titles[-1] == "✓ ene project"
    terminal.set_title_name(" new\n\x1bname ")
    assert titles[-1] == "✓ ene new name"
    terminal.close_title()


@pytest.mark.parametrize("session_label", ["my session", "20261009_052114"])
def test_terminal_keeps_context_status_when_activity_stops(monkeypatch, session_label):
    from prompt_toolkit.output import DummyOutput
    import prompt_toolkit.output.defaults as pt_defaults

    monkeypatch.setattr(pt_defaults, "create_output", lambda *a, **k: DummyOutput())
    terminal = TerminalInput(session_label=session_label)
    context = ContextStatus(96_000, 128_000, 200_000, 4_000, 150_000)
    terminal.set_context_status(context)
    terminal.set_status([
        ("class:status.spinner", "⠋ "),
        ("class:status.text", "Executing..."),
    ])

    active = "".join(text for _, text in terminal._message())
    assert "Executing..." in active
    assert f"75% · ↑200K · ↓4K · 75% hit · {session_label}" in active

    terminal.set_status([
        ("class:status.text", "Working..."),
        *context.prompt_fragments(),
    ])
    thinking = "".join(text for _, text in terminal._message())
    assert "75% hit" in thinking and thinking.count("75% hit") == 1
    assert thinking.count(session_label) == 1

    terminal.set_status(None)
    idle = "".join(text for _, text in terminal._message())
    assert "Executing..." not in idle
    assert f"75% · ↑200K · ↓4K · 75% hit · {session_label}" in idle
    lines = idle.splitlines()
    assert lines[0] and set(lines[0]) == {"─"}
    assert lines[2] and set(lines[2]) == {"─"}


@pytest.mark.parametrize("activity", [None, "Executing...", "Waiting...", "Compacting..."])
def test_terminal_model_marker_is_first_and_updates_while_idle(activity):
    from prompt_toolkit.output import DummyOutput
    from prompt_toolkit.application.current import create_app_session

    with create_app_session(output=DummyOutput()):
        terminal = TerminalInput(session_label="session")
    terminal.set_context_status(ContextStatus(0, 100, 0, 0, model="claude-opus-5-5"))
    terminal.set_status([("class:status.text", activity)] if activity else None)
    terminal.set_process_status("1 process running")
    lines = "".join(text for _, text in terminal._message()).splitlines()
    assert lines[1].startswith("✳︎ ")
    assert lines[1].count("✳︎") == 1
    assert ("class:status.model", "✳︎ ") in terminal._message()

    terminal.set_status(None)
    terminal.set_context_status(ContextStatus(0, 100, 0, 0, model="deepseek-v4"))
    lines = "".join(text for _, text in terminal._message()).splitlines()
    assert lines[1].startswith("🐋 ")
    assert "✳︎" not in lines[1]
    terminal.close_title()


def test_terminal_updates_session_label_before_multiline_process_status():
    from prompt_toolkit.output import DummyOutput
    from prompt_toolkit.application.current import create_app_session

    with create_app_session(output=DummyOutput()):
        terminal = TerminalInput(session_label="old name")
    terminal.set_context_status(ContextStatus(1, 10, 0, 0))
    terminal.set_process_status("1 process running\n└ reading files")
    terminal.set_session_label(" new\n\x1bname ")

    text = "".join(text for _, text in terminal._message())
    lines = text.splitlines()
    assert lines[1].startswith("1 process running ")
    assert lines[1].endswith("0% hit · new name")
    assert lines[2] == "└ reading files"
    assert "old name" not in text
    terminal.close_title()


def test_thinking_indicator_publishes_running_suffix_updates():
    events = []

    class Hub:
        def publish(self, kind, **data):
            events.append((kind, data))

    indicator = ThinkingIndicator(
        Console(file=io.StringIO(), no_color=True),
        events=Hub(),
        render_terminal=False,
    )
    indicator.__enter__()
    indicator.set_status_suffix("1/3 completed\n└ 2 · image.png")
    indicator.__exit__(None, None, None)

    assert ("thinking_update", {"suffix": "1/3 completed\n└ 2 · image.png"}) in events


def test_thinking_indicator_can_render_a_countdown():
    output = io.StringIO()
    console = Console(file=output, width=80, no_color=True)
    indicator = ThinkingIndicator(console, countdown=61, label="Waiting")

    assert indicator._label_plain(0) == "Waiting... (1m 01s)"
    assert indicator._label_plain(1.1) == "Waiting... (1m 00s)"
    assert indicator._label_plain(99) == "Waiting... (0s)"
    status = "".join(text for _, text in indicator._prompt_status("⠋", 1.1))
    assert "Waiting... (1m 00s)" in status


def test_thinking_indicator_resumes_remote_elapsed_time(monkeypatch):
    times = iter([110.0, 210.0])
    monkeypatch.setattr("ene.ui.time.monotonic", lambda: next(times))
    indicator = ThinkingIndicator(
        Console(file=io.StringIO(), no_color=True),
        initial_elapsed=10,
        render_terminal=False,
    )

    indicator.__enter__()
    assert indicator._start_time == 100.0
    assert indicator._label_plain(10) == "Working... (10s)"
    indicator.__exit__(None, None, None)


def test_thinking_indicator_shows_frozen_accumulated_round_time():
    output = io.StringIO()
    console = Console(file=output, width=80, no_color=True)
    context = ContextStatus(13, 100, 226_000, 5_000, 180_800)
    indicator = ThinkingIndicator(
        console, status_suffix=context, round_elapsed=312.9
    )

    assert indicator._label_plain(12) == (
        "Working... (12s) · 13% · ↑226K · ↓5K · 80% hit · round 5m"
    )
    status = "".join(text for _, text in indicator._prompt_status("⠋", 12))
    assert status.endswith(" · round 5m")
    # The prompt draws context usage itself, right-aligned.
    assert "80% hit" not in status


def test_console_freezes_round_time_when_each_indicator_starts(monkeypatch):
    times = iter([100.0, 112.0, 130.0])
    monkeypatch.setattr("ene.ui.time.monotonic", lambda: next(times))
    console = AgentConsole()

    with console.round_timer():
        first = console.thinking()
        second = console.thinking(label="Executing")

    assert first._round_elapsed == 12.0
    assert second._round_elapsed == 30.0


def make_stream():
    output = io.StringIO()
    console = Console(file=output, width=80, no_color=True)
    return output, ResponseStream(console, None)


def test_response_stream_writes_completed_blocks_before_close():
    output, stream = make_stream()

    stream.on_content("Hello **world**\n\nPending")

    assert "Hello world" in output.getvalue()
    assert "Pending" not in output.getvalue()
    stream.close()
    assert "Pending" in output.getvalue()
    assert output.getvalue().count("Hello world") == 1


def test_response_stream_renders_block_markdown_across_chunks():
    output, stream = make_stream()
    chunks = [
        "# Ti",
        "tle\n- first\n1. sec",
        "ond\n| Name | Value |\n| --- | --- |\n| a | 1 |\n",
        "```python\ndef hi():\n    return 1\n```",
    ]

    for chunk in chunks:
        stream.on_content(chunk)
    stream.close()

    rendered = output.getvalue()
    assert "Title" in rendered
    assert "• first" in rendered
    assert "second" in rendered
    assert "Name" in rendered and "Value" in rendered
    assert "def hi():" in rendered
    assert "return 1" in rendered
    assert "```" not in rendered


def test_response_stream_keeps_streamed_list_items_compact():
    output, stream = make_stream()

    stream.on_content("- first\n- second\n- third\n")
    stream.close()

    lines = output.getvalue().splitlines()
    assert lines[0].startswith("💠 ")
    assert [line.strip() for line in lines[1:]] == [
        "• first", "• second", "• third",
    ]


def test_response_stream_preserves_nested_list_indentation():
    from rich.cells import cell_len

    output, stream = make_stream()

    stream.on_content(
        "- parent\n"
        "  - child one\n"
        "  - child two\n"
        "- sibling\n"
    )
    stream.close()

    # Compare display columns (the 💠 brand marker is one char but two cells).
    lines = output.getvalue().splitlines()
    positions = {
        text: next(
            cell_len(line[: line.rindex("•")]) for line in lines if text in line
        )
        for text in ("parent", "child one", "child two", "sibling")
    }
    assert positions["child one"] == positions["child two"]
    assert positions["child one"] > positions["parent"]
    assert positions["sibling"] == positions["parent"]


def test_response_stream_commits_closed_top_level_fence():
    output, stream = make_stream()

    stream.on_content("```python\nprint('done')\n```\nPending")

    assert "print('done')" in output.getvalue()
    assert "Pending" not in output.getvalue()


def test_response_stream_keeps_fenced_code_inside_list():
    output, stream = make_stream()

    stream.on_content(
        "- parent\n\n"
        "  ```python\n"
        "  print('nested')\n"
        "  ```\n\n"
        "- sibling\n\n"
        "after\n\n"
    )

    rendered = output.getvalue()
    assert "parent" in rendered and "print('nested')" in rendered
    assert "sibling" in rendered and "after" in rendered
    assert rendered.index("parent") < rendered.index("print('nested')")
    assert rendered.index("print('nested')") < rendered.index("sibling")
    assert rendered.count("parent") == 1


def test_response_stream_keeps_blockquote_inside_list():
    output, stream = make_stream()

    stream.on_content(
        "- parent\n\n"
        "  > quoted\n"
        "  > continuation\n\n"
        "- sibling\n\n"
        "after\n\n"
    )

    rendered = output.getvalue()
    assert "parent" in rendered
    assert "quoted continuation" in rendered
    assert "sibling" in rendered and "after" in rendered
    assert rendered.count("parent") == 1


def test_response_stream_does_not_commit_unclosed_fence():
    output, stream = make_stream()

    stream.on_content("```python\nprint('pending')\n")
    assert output.getvalue() == ""

    stream.on_content("```\n")
    assert "print('pending')" in output.getvalue()


@pytest.mark.parametrize(
    "chunks",
    [
        ["```p", "ython\ndef foo():\n", "    return 1\n", "```\n", "After\n"],
        ["``", "`python\ndef foo():\n", "    return 1\n", "```\n", "After\n"],
        ["```", "python\ndef foo():\n", "    return 1\n", "```\n", "After\n"],
        ["~~~java", "script\nconsole.log(1)\n", "~~~\n", "After\n"],
    ],
)
def test_response_stream_fence_opener_split_across_chunks(chunks):
    output, stream = make_stream()

    for chunk in chunks:
        stream.on_content(chunk)
    stream.close()

    rendered = output.getvalue()
    expected = "console.log(1)" if chunks[0].startswith("~") else "def foo():"
    assert expected in rendered
    assert "return 1" in rendered or "console.log(1)" in rendered
    assert "After" in rendered
    assert "```" not in rendered
    assert "~~~" not in rendered
    assert rendered.index(expected) < rendered.index("After")


@pytest.mark.parametrize(
    "chunks",
    [
        ["```text\nvalue```\nafter\n"],
        ["```text\nvalue", "```\nafter\n"],
        ["```text\nvalue`", "``\nafter\n"],
    ],
)
def test_response_stream_chunk_boundaries_do_not_create_fence_newlines(chunks):
    output, stream = make_stream()

    for chunk in chunks:
        stream.on_content(chunk)
    stream.close()

    rendered = output.getvalue()
    assert "value```" in rendered
    assert "after" in rendered


@pytest.mark.parametrize("indent", ["    ", "\t"])
def test_response_stream_does_not_close_fence_at_indented_marker(indent):
    output, stream = make_stream()

    stream.on_content(f"```text\nvalue\n{indent}```\nstill code\n")
    assert output.getvalue() == ""

    stream.on_content("```\nafter\n")
    rendered = output.getvalue()
    assert "value" in rendered
    assert "```" in rendered
    assert "still code" in rendered
    assert "after" not in rendered


def test_response_stream_keeps_backticks_inside_fence_content():
    output, stream = make_stream()

    # A backtick run inside the content (e.g. a quoted ```) is not a fence
    # line: it arrives without its own newline, so it must not be split off.
    for chunk in ["```python\n", "s = \"", "```", "\"\n", "```\n"]:
        stream.on_content(chunk)
    stream.close()

    rendered = output.getvalue()
    assert "s = \"```\"" in rendered
    assert "```" not in rendered.replace("s = \"```\"", "")


def test_response_stream_waits_for_late_reference_definition():
    output, stream = make_stream()

    stream.on_content("See [the docs][docs].\n\nAnother block.\n\n")
    assert output.getvalue() == ""

    stream.on_content("[docs]: https://example.com/docs\n\nDone.\n\n")
    rendered = output.getvalue()
    assert "See the docs." in rendered
    assert "Another block." in rendered
    assert "[docs]" not in rendered
    assert rendered.count("See the docs.") == 1


def test_response_stream_preserves_literal_asterisks_and_inline_code():
    output, stream = make_stream()

    for chunk in ["2 *", " 3 and `a*", "b*` and *italic", "*"]:
        stream.on_content(chunk)
    stream.close()

    assert "2 * 3 and a*b* and italic" in output.getvalue()


def test_response_stream_renders_table_without_leading_pipes():
    output, stream = make_stream()

    stream.on_content("A | B\n---|---\n1 | 2\n\n")
    stream.close()

    rendered = output.getvalue()
    assert "A" in rendered and "B" in rendered
    assert "1" in rendered and "2" in rendered
    # The table must be rendered by rich (pipes replaced by a rule line), but
    # the exact box character varies across rich versions (─ vs ━).
    assert "|" not in rendered
    assert any(ch in rendered for ch in ("─", "━"))


def test_response_stream_keeps_text_after_unterminated_table():
    output, stream = make_stream()

    stream.on_content("| A | B |\n|---|---|\n| 1 | 2 |\nafter")
    stream.close()

    rendered = output.getvalue()
    assert "1" in rendered and "2" in rendered
    assert "after" in rendered


def test_response_stream_commits_complete_thinking_lines():
    output = io.StringIO()
    console = Console(file=output, width=80, no_color=True)
    stream = ResponseStream(console, None, show_thinking=True)

    stream.on_thinking("first partial")
    assert output.getvalue() == ""

    stream.on_thinking(" completed\nsecond partial")
    assert output.getvalue() == "  ┊ first partial completed\n"

    stream.on_content("answer")
    assert output.getvalue() == "  ┊ first partial completed\n  ┊ second partial\n"

    stream.close()
    assert "answer" in output.getvalue()


def test_response_stream_discards_pending_thinking_on_abort():
    output = io.StringIO()
    console = Console(file=output, width=80, no_color=True)
    stream = ResponseStream(console, None, show_thinking=True)

    stream.on_thinking("complete\npending")
    stream.close(render_terminal=False)

    assert output.getvalue() == "  ┊ complete\n"


def test_completed_thinking_message_renders_and_emits_event():
    from ene.ui import AgentConsole
    from ene.utils.io import EventHub

    output = io.StringIO()
    console = AgentConsole()
    console._console = Console(file=output, width=80, no_color=True)
    events = EventHub()
    console.events = events

    console.thinking_message("first line\nsecond line")

    assert output.getvalue() == "  ┊ first line\n  ┊ second line\n"
    event = events.after(0)[-1]
    assert event.type == "thinking"
    assert event.data["text"] == "first line\nsecond line"


def test_console_stream_output_writes_raw_block_and_emits_event():
    from ene.ui import AgentConsole
    from ene.utils.io import EventHub

    output = io.StringIO()
    console = AgentConsole()
    console._console = Console(file=output, width=80, force_terminal=True, color_system="truecolor")
    events = EventHub()
    console.events = events

    # Command output is data: markup and ANSI escapes pass through untouched.
    console.stream_output("a\n[/bad] [dim] tag\n\x1b[31mred\x1b[0m")

    rendered = output.getvalue()
    assert "[/bad] [dim] tag" in rendered
    assert "\x1b[31mred\x1b[0m" in rendered
    assert rendered.startswith("\x1b[2m")  # dim-wrapped on ANSI terminals

    evs = events.after(0)
    assert evs and evs[-1].type == "output"
    assert evs[-1].data["text"] == "a\n[/bad] [dim] tag\n\x1b[31mred\x1b[0m"


def test_console_print_event_includes_plain_and_ansi_renderings():
    from ene.utils.io import EventHub

    events = EventHub()
    console = AgentConsole(events=events, render_terminal=False)
    console.print("[bold yellow]user[/bold yellow] [color(244)]tool[/color(244)]")

    event = events.snapshot()[-1]
    assert event.type == "output"
    assert event.data["text"] == "user tool"
    assert "\x1b[" in event.data["ansi"]
    assert "user" in event.data["ansi"] and "tool" in event.data["ansi"]


def test_headless_console_suppression_hides_events_and_nested_indicators():
    from ene.utils.io import EventHub

    events = EventHub()
    console = AgentConsole(events=events, render_terminal=False)

    with console.suppressed():
        console.system("hidden system")
        console.tool_result("hidden tool result")
        console.response("hidden assistant")
        with console.thinking():
            pass
        with console.stream_response() as stream:
            stream.on_content("hidden stream")
        console.stream_output("hidden output")

    assert events.snapshot() == []

    with console.suppressed():
        with console.visible():
            console.system("visible system")
            with console.thinking():
                pass

    assert [event.type for event in events.snapshot()] == [
        "system", "thinking_start", "thinking_stop",
    ]


def test_console_stream_output_is_plain_when_not_ansi():
    from ene.ui import AgentConsole

    output = io.StringIO()
    console = AgentConsole()
    console._console = Console(file=output, width=80, no_color=True)

    console.stream_output("plain line")
    console.stream_output("")

    assert output.getvalue() == "plain line\n"


def test_response_stream_coalesces_web_deltas_and_flushes_tail():
    from ene.utils.io import EventHub

    events = EventHub()
    published = []
    events.add_listener(lambda event: published.append(event.type))
    console = Console(file=io.StringIO(), width=80, no_color=True)
    stream = ResponseStream(console, events, show_thinking=True)
    for token in ["Hel", "lo ", "wor", "ld"]:
        stream.on_content(token)
    deltas = [e.data["text"] for e in events.snapshot() if e.type == "assistant_delta"]
    # The first fragment is sent at once; the rest wait for the batch window.
    assert deltas[0] == "Hel"
    assert len(deltas) < 4
    time.sleep(ResponseStream.DELTA_INTERVAL_SECONDS * 3)
    deltas = [e.data["text"] for e in events.snapshot() if e.type == "assistant_delta"]
    assert "".join(deltas) == "Hello world"
    stream.on_thinking("hmm")
    stream.close()
    # Fragments are flushed in order before the consolidated events.
    assert published.index("thinking_delta") < published.index("thinking")
    assert published[-1] == "assistant_message"


def test_quiet_response_stream_skips_terminal_markdown(monkeypatch):
    console = Console(file=io.StringIO(), width=80, no_color=True)
    stream = ResponseStream(console, None, render_terminal=False)
    monkeypatch.setattr(
        stream._terminal, "feed", lambda text: pytest.fail("quiet stream must not render")
    )
    stream.on_content("- item\n")
    stream.close()


def test_thinking_indicator_exits_without_waiting_for_tick():
    console = AgentConsole()
    start = time.monotonic()
    for _ in range(5):
        with console.thinking():
            pass
    assert time.monotonic() - start < 0.4


def _plain_console():
    from ene.ui import AGENT_THEME, AgentConsole
    from ene.utils.io import EventHub

    output = io.StringIO()
    console = AgentConsole()
    console._console = Console(file=output, width=60, no_color=True, theme=AGENT_THEME)
    console.events = EventHub()
    return output, console


def test_user_and_response_headers_carry_message_time():
    stamp = float(int(time.time()) - 120)
    clock = lambda value: time.strftime("%H:%M:%S", time.localtime(value))  # noqa: E731
    if time.strftime("%Y-%m-%d", time.localtime(stamp + 60)) != time.strftime("%Y-%m-%d"):
        pytest.skip("too close to midnight for a same-day timestamp")
    output, console = _plain_console()

    console.user_input("first line\nsecond", with_rule=False, created_at=stamp)
    console.response("Answer **here**", created_at=stamp + 60, model="gpt-5.5")
    console.user_input("from an old session", with_rule=False, created_at=None)
    console.response("old reply", created_at=None)

    assert output.getvalue().splitlines() == [
        f"❯ You · {clock(stamp)}",
        "  first line",
        "  second",
        f"💠 gpt-5.5 · {clock(stamp + 60)}",
        "  Answer here",
        "❯ You",
        "  from an old session",
        "💠 Assistant",
        "  old reply",
    ]
    events = console.events.after(0)
    assert [event.data["created_at"] for event in events] == [stamp, stamp + 60, None, None]
    assert events[1].data["model"] == "gpt-5.5"


def test_response_header_shows_date_for_earlier_days():
    output, console = _plain_console()
    console.response("old", created_at=time.mktime((2026, 1, 2, 12, 0, 0, 0, 0, -1)))
    assert output.getvalue().splitlines()[0] == "💠 Assistant · 2026-01-02 12:00"


def test_tool_lines_nest_results_and_wrap_under_their_indent():
    output, console = _plain_console()

    console.tool("read", name="read", primary="ene/ui.py", qualifiers=["1-200"])
    console.tool_result("exit 1\nFAILED tests/test_x.py", success=False)
    console.warn("careful " * 12)

    lines = output.getvalue().splitlines()
    assert lines[0] == "  • read ene/ui.py · 1-200"
    assert lines[1] == "    └ ✗ exit 1"
    assert lines[2] == "        FAILED tests/test_x.py"
    assert lines[3].startswith("  ! careful")
    assert len(lines) > 4 and all(line.startswith("    careful") for line in lines[4:])
    assert all(line == line.rstrip() for line in lines)


def test_streamed_reply_is_stamped_when_its_first_text_arrives():
    from ene.utils.io import EventHub

    output = io.StringIO()
    hub = EventHub()
    stream = ResponseStream(Console(file=output, width=60, no_color=True), hub, model="m1")

    started = time.mktime((2026, 1, 2, 12, 0, 0, 0, 0, -1))
    stream.on_content("\n", at=started - 60)  # invisible: does not start the reply
    stream.on_content("Hello\n\n", at=started)
    stream.close()

    assert stream.created_at == started
    assert output.getvalue().splitlines()[0] == "💠 m1 · 2026-01-02 12:00"
    final = [event for event in hub.after(0) if event.type == "assistant_message"]
    assert final[-1].data["created_at"] == started
    assert final[-1].data["model"] == "m1"

    published = []
    recorder = SimpleNamespace(publish=lambda kind, **data: published.append((kind, data)))
    relay = ResponseStream(Console(file=io.StringIO()), recorder, model="m1")
    relay.on_content("Hi\n")
    relay.close()
    assert ("assistant_delta", {"text": "Hi\n", "model": "m1"}) in published


def test_round_summary_reports_timing_and_tokens():
    from ene.ui import format_round_summary

    record = {
        "model_calls": 3, "duration": 95, "tool_calls": 6, "tool_failures": 1,
        "ttft": 0.85, "tps": 52.3, "input_tokens": 120_000,
        "output_tokens": 3_400, "cached_tokens": 99_600, "cancelled": False,
    }
    assert format_round_summary(record) == (
        "✓ 1m 35s · 6 tools (1 failed) · TTFT 850ms · 52 tok/s · ↑120K ↓3K · 83% cached"
    )
    assert format_round_summary({**record, "cancelled": True}).startswith("■ stopped 1m 35s")
    assert format_round_summary({**record, "model_calls": 0}) == ""

    output, console = _plain_console()
    console.round_summary(record)
    assert output.getvalue().startswith("  ✓ 1m 35s")
    # Terminal only: web clients render the round_stats event themselves.
    assert console.events.after(0) == []


def test_thinking_indicator_shows_time_to_first_token_and_output_rate(monkeypatch):
    times = iter([100.0, 101.8])
    monkeypatch.setattr("ene.ui.time.monotonic", lambda: next(times))
    events = []

    class Hub:
        def publish(self, kind, **data):
            events.append((kind, data))

    indicator = ThinkingIndicator(
        Console(file=io.StringIO(), no_color=True), events=Hub(), render_terminal=False
    )
    indicator.__enter__()
    indicator.set_first_token()
    indicator.set_first_token()  # only the first token counts
    indicator.set_output_rate(52.4)
    status = "".join(text for _, text in indicator._prompt_status("⠋", 12))
    indicator.__exit__(None, None, None)

    assert status == "⠋ Working... (12s) · TTFT 1.8s · 52 tok/s"
    assert ("thinking_update", {"ttft": 1.8}) in events
    assert sum(kind == "thinking_update" and "ttft" in data for kind, data in events) == 1


def test_terminal_status_right_aligns_model_and_context():
    from prompt_toolkit.output import DummyOutput
    from prompt_toolkit.application.current import create_app_session

    with create_app_session(output=DummyOutput()):
        terminal = TerminalInput(session_label="demo")
    terminal._session = SimpleNamespace(app=SimpleNamespace(
        output=SimpleNamespace(get_size=lambda: SimpleNamespace(columns=101)),
        is_running=False,
    ))
    terminal.set_context_status(ContextStatus(10, 100, 0, 0, model="gpt-5.5"))
    terminal.set_status([("class:status.text", "Working...")])

    line = "".join(text for _, text in terminal._message()).splitlines()[1]
    from rich.cells import cell_len

    assert cell_len(line) == 100  # flush with the separator above
    # The model marker and name stay together on the left.
    assert line.startswith("🔷 gpt-5.5 · Working...  ")
    assert line.endswith(" ━━━━━━━━━━━━━━ · 10% · ↑0 · ↓0 · 0% hit · demo")

    # Narrower: the right side drops details, then disappears, never wraps.
    terminal._session.app.output.get_size = lambda: SimpleNamespace(columns=60)
    medium = "".join(text for _, text in terminal._message()).splitlines()[1]
    assert cell_len(medium) == 59  # flush with the separator
    assert medium.endswith(" ━━━━━━━━━━━━━━ 10% · demo")

    terminal._session.app.output.get_size = lambda: SimpleNamespace(columns=30)
    narrow = "".join(text for _, text in terminal._message()).splitlines()[1]
    assert narrow == "🔷 gpt-5.5 · Working..."

    terminal._session.app.output.get_size = lambda: SimpleNamespace(columns=101)
    terminal.set_status(None)
    idle = "".join(text for _, text in terminal._message()).splitlines()[1]
    assert idle.startswith("🔷 gpt-5.5  ")
    terminal.close_title()
