import io

from rich.console import Console

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
        "Working... (12s) · 13% · ↑226K · ↓5K · 80% hit · 5m"
    )
    status = "".join(text for _, text in indicator._prompt_status("⠋", 12))
    assert status.endswith(" · 5m")


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
    assert [line.strip() for line in lines] == ["•  • first", "• second", "• third"]


def test_response_stream_preserves_nested_list_indentation():
    output, stream = make_stream()

    stream.on_content(
        "- parent\n"
        "  - child one\n"
        "  - child two\n"
        "- sibling\n"
    )
    stream.close()

    lines = output.getvalue().splitlines()
    positions = {
        text: next(line.rindex("•") for line in lines if text in line)
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
    assert output.getvalue() == "first partial completed\n"

    stream.on_content("answer")
    assert output.getvalue() == "first partial completed\nsecond partial\n"

    stream.close()
    assert "answer" in output.getvalue()


def test_response_stream_discards_pending_thinking_on_abort():
    output = io.StringIO()
    console = Console(file=output, width=80, no_color=True)
    stream = ResponseStream(console, None, show_thinking=True)

    stream.on_thinking("complete\npending")
    stream.close(render_terminal=False)

    assert output.getvalue() == "complete\n"


def test_completed_thinking_message_renders_and_emits_event():
    from ene.ui import AgentConsole
    from ene.utils.io import EventHub

    output = io.StringIO()
    console = AgentConsole()
    console._console = Console(file=output, width=80, no_color=True)
    events = EventHub()
    console.events = events

    console.thinking_message("first line\nsecond line")

    assert output.getvalue() == "first line\nsecond line\n"
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
