"""Tests for per-tool output retention during context compaction."""

import json

import pytest

from ene.context import (
    COMPACTION_KEEP_RECENT_MAX_TOKENS,
    COMPACTION_MIN_YIELD_RATIO,
    SUMMARY_MARKER,
    TokenEstimator,
    ToolResultEnvelope,
    CompactionState,
    compact_context,
    compact_tool_result_envelope,
    compaction_trigger_tokens,
    needs_compaction,
    request_output_tokens,
)
from ene.context import _TRANSCRIPT_HEADING
from ene.messages import Message, ToolCall


@pytest.mark.parametrize(
    "tool_name",
    ["read_file", "web_fetch", "ls", "glob_files", "grep_files"],
)
def test_document_and_search_compaction_keeps_prefix(tool_name):
    text = "BEGINNING\n" + "middle\n" * 3000 + "END\n"

    result = compact_tool_result_envelope(
        ToolResultEnvelope(tool_name, {}, {}, text),
        16_000,
    )

    assert result.retained_chars < result.original_chars
    assert "BEGINNING" in result.text
    assert "END" not in result.text


def test_exec_compaction_keeps_both_ends_of_command_output():
    """Command output is sampled at both ends, not tail-only.

    stdout and stderr share one pipe and a piped child block-buffers stdout
    while stderr stays unbuffered, so a diagnostic routinely lands ahead of the
    bulk output rather than after it.
    """
    text = "BEGINNING\n" + "".join(f"middle {i}\n" for i in range(3000)) + "ERROR: failed\nLATEST\n"

    result = compact_tool_result_envelope(
        ToolResultEnvelope("exec_command", {"command": "custom"}, {}, text),
        16_000,
    )

    assert result.retained_chars < result.original_chars
    assert "BEGINNING" in result.text
    assert "middle 1500" not in result.text
    assert "ERROR: failed" in result.text
    assert "LATEST" in result.text


def test_process_compaction_keeps_status_and_latest_log():
    process_result = {
        "processes": [{
            "process_id": "1",
            "status": "exited",
            "exit_code": 1,
            "log_tail": "old log line\n" * 1000 + "LATEST LOG LINE\n",
            "log_tail_truncated": True,
        }],
        "success": True,
    }

    result = compact_tool_result_envelope(
        ToolResultEnvelope(
            "inspect_processes",
            {},
            process_result,
            json.dumps(process_result, indent=2),
        ),
        16_000,
    )

    assert result.retained_chars < result.original_chars
    assert '"status": "exited"' in result.text
    assert '"exit_code": 1' in result.text
    assert "LATEST LOG LINE" in result.text


# ---------------------------------------------------------------------------
# Token accounting
# ---------------------------------------------------------------------------


def test_prompt_tokens_anchor_beats_a_lagging_ratio():
    """The anchor, not the smoothed ratio, decides live usage.

    Character counting cannot see tool schemas or wire framing, so once the API
    has reported a real count the estimate must start from it and only estimate
    the delta.
    """
    estimator = TokenEstimator()

    estimator.observe(100_000, 25_000)  # ratio snaps to 4.0 on first observation
    assert estimator.prompt_tokens(100_000) == 25_000
    assert estimator.prompt_tokens(140_000) == 35_000
    assert estimator.prompt_tokens(60_000) == 15_000  # shrinks after compaction

    estimator.observe(100_000, 50_000)  # ratio only eases toward 2.0 via EMA
    assert estimator.chars_to_tokens(100_000) != 50_000
    assert estimator.prompt_tokens(100_000) == 50_000


def test_unanchored_estimator_falls_back_to_the_ratio():
    assert TokenEstimator(initial=4.0).prompt_tokens(40_000) == 10_000


def test_compaction_trigger_reserves_output_headroom():
    # Large window: the ratio governs, not the (relatively tiny) reserve.
    assert compaction_trigger_tokens(1_000_000, max_output_tokens=32_000) == 850_000
    # Typical window: reserve keeps the trigger below the flat ratio.
    assert compaction_trigger_tokens(200_000, max_output_tokens=32_000) == 160_000
    # Small window with a huge output cap: the floor prevents constant compaction.
    assert compaction_trigger_tokens(32_000, max_output_tokens=16_000) == 16_000
    # A huge output cap reserves at most 32k: gpt-5 no longer compacts at half
    # its window, and a 1M window is governed by the ratio alone.
    assert compaction_trigger_tokens(258_000, max_output_tokens=128_000) == 218_000
    assert compaction_trigger_tokens(1_000_000, max_output_tokens=128_000) == 850_000


def test_request_output_cap_shrinks_only_when_it_would_overrun_the_window():
    # Plenty of room: the configured cap is sent unchanged.
    assert request_output_tokens(1_000_000, 128_000, 850_000) == 128_000
    # Near gpt-5's trigger, prompt + 128k would overrun 258k: shrink to fit,
    # leaving 2% of the window for estimate error.
    assert request_output_tokens(258_000, 128_000, 218_000) == 258_000 - 218_000 - 5_160
    # Past the end of the window, still ask for a usable reply.
    assert request_output_tokens(128_000, 32_000, 130_000) == 4_096
    assert request_output_tokens(128_000, 2_000, 130_000) == 2_000
    # Unknown window: nothing to fit against.
    assert request_output_tokens(0, 32_000, 10**9) == 32_000


def test_unknown_context_length_never_forces_compaction():
    assert not needs_compaction([Message.user("x" * 10_000)], 0)


@pytest.mark.parametrize(
    "context_length,max_output_tokens",
    [
        (128_000, 32_000),   # typical window
        (1_000_000, 64_000),  # large window, ratio governs
        (258_000, 128_000),  # gpt-5: reserve drags the trigger to its floor
        (32_000, 16_000),    # small window, huge output cap
    ],
)
def test_full_handoff_lands_far_below_its_own_trigger(context_length, max_output_tokens):
    """A pass replaces the whole history, so the next one is a window away.

    The old sliding split aimed at half the window, which on gpt-5 put the
    target on the trigger itself and bought a compaction every few rounds.
    """
    cpt = 4.0
    trigger = compaction_trigger_tokens(context_length, max_output_tokens)
    messages = [Message.user("build the feature")]
    i = 0
    while sum(m.chars for m in messages) / cpt <= trigger:
        messages += _turn(f"c{i}", "read_file", {"file": f"f{i}.py"}, "x" * 8_000)
        i += 1
    messages.append(Message.assistant("done"))

    result, _ = compact_context(
        messages, lambda prompt: "## Goal\ng",
        context_length=context_length, chars_per_token=cpt,
    )

    after = sum(m.chars for m in result) / cpt
    # Only the small recent slice survives next to the handoff.
    assert after <= COMPACTION_KEEP_RECENT_MAX_TOKENS + 2_000
    assert trigger - after >= context_length * COMPACTION_MIN_YIELD_RATIO
    assert result[-1].text == "done"


def test_a_split_too_small_to_pay_for_itself_spends_no_round_trip():
    """Real pressure is not enough; the split still has to free more than it writes."""
    messages = [Message.user("start")]
    for i in range(3):  # old and cheap — all that sits outside the keep window
        messages += _turn(f"s{i}", "read_file", {"file": f"s{i}.py"}, "x" * 1_000)
    for i in range(12):  # recent and bulky — protected from summarization
        messages += _turn(f"b{i}", "read_file", {"file": f"b{i}.py"}, "x" * 5_000)
    messages.append(Message.assistant("done"))
    calls = []

    result, _ = compact_context(
        messages, lambda prompt: calls.append(prompt) or "## Goal\ng",
        context_length=CONTEXT_LENGTH, chars_per_token=CHARS_PER_TOKEN,
    )

    # The summarizable half is ~3k chars; the summary written back in its place
    # is allowed up to 32k. Compacting here would *grow* the context.
    assert not calls
    assert result is messages


CONTEXT_LENGTH = 100_000
CHARS_PER_TOKEN = 4.0


def _turn(call_id, name, arguments, output):
    return [
        Message.assistant(
            content="",
            tool_calls=[ToolCall(call_id, name, json.dumps(arguments))],
        ),
        Message.tool(call_id, output),
    ]


# ---------------------------------------------------------------------------
# Compaction
# ---------------------------------------------------------------------------


def _capture(reply="## Goal\nship it"):
    seen = {}

    def summarize(prompt):
        seen["prompt"] = prompt
        return reply

    return seen, summarize


def test_original_request_is_carried_verbatim_across_compactions():
    request = "Refactor ene/context.py to fix the eviction thresholds"
    messages = [Message.user(request)]
    for i in range(6):
        messages += _turn(f"c{i}", "read_file", {"file": f"f{i}.py"}, "x" * 200)
    messages.append(Message.assistant("done"))

    seen, summarize = _capture()
    first, state = compact_context(messages, summarize)
    assert f"## Original request\n{request}" in first[0].text

    # Compacting the result again must not paraphrase or drop the request.
    second_input = first + [
        Message.user("keep going"),
        Message.assistant("ok"),
    ]
    seen, summarize = _capture()
    second, _ = compact_context(second_input, summarize, state=state)
    assert f"## Original request\n{request}" in second[0].text


def test_recompaction_updates_the_previous_summary_instead_of_reducing_it():
    prior = (
        f"{SUMMARY_MARKER}\nhandoff\n\n## Original request\nbuild it\n\n"
        "## Goal\nship the feature"
    )
    messages = [
        Message.user(prior),
        Message.assistant("worked on it"),
        Message.user("continue"),
        Message.assistant("more work"),
    ]

    seen, summarize = _capture()
    compact_context(messages, summarize)

    prompt = seen["prompt"]
    assert "<previous-summary>" in prompt
    assert "ship the feature" in prompt
    assert "PRESERVE every still-relevant fact" in prompt
    # The prior summary is supplied once, as the thing being updated.
    assert prompt.count("ship the feature") == 1


def _filler(count):
    """Trailing messages so the 40% split reaches the turns under test."""
    return [Message.assistant(f"step {i}") for i in range(count)]


def test_summary_input_drops_the_middle_not_the_tail():
    messages = [Message.user("FIRST-REQUEST")]
    for i in range(400):
        messages += _turn(f"c{i}", "read_file", {"file": f"f{i}.py"}, f"MID{i} " + "x" * 900)

    seen, summarize = _capture()
    compact_context(messages, summarize)

    prompt = seen["prompt"]
    # The oldest entries anchor what the session set out to do...
    assert "FIRST-REQUEST" in prompt
    assert "MID0 " in prompt
    # ...and the newest of the compacted range carry the current state.
    assert "MID398 " in prompt
    # The in-flight round is kept verbatim, not summarized.
    assert "MID399 " not in prompt
    # The middle is what gets sacrificed.
    assert "MID200 " not in prompt
    assert "middle of the conversation omitted" in prompt


def test_summary_carries_file_and_skill_state_without_contents():
    messages = [Message.user("start")]
    messages += _turn("c0", "read_file", {"file": "a.py"}, "CONTENTS OF A")
    messages += _turn("c1", "edit_file", {"file": "b.py"}, "ok")
    messages += _turn("c2", "load_skill", {"name": "monitor"}, "skill text")
    messages += _filler(12)

    seen, summarize = _capture()
    result, _ = compact_context(messages, summarize)

    content = result[0].text
    assert "- Modified: b.py" in content
    assert "- Read: a.py" in content
    assert "CONTENTS OF A" not in content  # a list, never the contents
    assert "- Active: monitor" in content


def test_file_lists_merge_across_compactions_and_track_writes():
    messages = [Message.user("start")]
    messages += _turn("c0", "read_file", {"file": "a.py"}, "x")
    messages += _turn("c1", "read_file", {"file": "b.py"}, "x")
    messages += _filler(10)

    seen, summarize = _capture()
    first, state = compact_context(messages, summarize)
    assert "- Read: a.py, b.py" in first[0].text

    second_input = first[:1] + list(_turn("c2", "edit_file", {"file": "a.py"}, "ok"))
    second_input += _filler(10)
    seen, summarize = _capture()
    second, _ = compact_context(second_input, summarize, state=state)

    content = second[0].text
    assert "- Modified: a.py" in content
    assert "- Read: b.py" in content  # a.py moved out of the read list


def test_compaction_replaces_everything_but_a_small_recent_slice():
    messages = [Message.user("start")]
    for i in range(30):
        messages += _turn(f"c{i}", "read_file", {"file": f"f{i}.py"}, f"R{i} " + "x" * 3_000)
    messages.append(Message.assistant("done"))

    seen, summarize = _capture()
    result, _ = compact_context(
        messages, summarize,
        context_length=CONTEXT_LENGTH,
        chars_per_token=CHARS_PER_TOKEN,
    )

    assert result[0].text.startswith(SUMMARY_MARKER)
    # keep-recent is min(5% of 100k, 12k) = 5k tokens = 20k chars: the newest
    # whole rounds that fit survive verbatim, with the final reply.
    kept = result[1:]
    assert 12_000 <= sum(m.chars for m in kept) <= 20_000
    assert kept[0].is_assistant and kept[0].tool_calls  # never starts mid-pair
    assert kept[-1].text == "done"
    assert "R29 " not in seen["prompt"] and "R20 " in seen["prompt"]


def test_in_flight_tool_results_survive_whatever_their_size():
    """Results the model has not seen yet cannot be summarized away."""
    messages = [Message.user("start")]
    for i in range(10):
        messages += _turn(f"c{i}", "read_file", {"file": f"f{i}.py"}, "x" * 20_000)
    messages += _turn("live", "exec_command", {"command": "pytest"}, "LIVE " + "y" * 30_000)

    result, _ = compact_context(
        messages, lambda prompt: "## Goal\ng",
        context_length=CONTEXT_LENGTH, chars_per_token=CHARS_PER_TOKEN,
    )

    assert len(result) == 3
    assert result[1].tool_calls[0].id == "live"
    assert result[2].text.startswith("LIVE ")


def test_unanswered_prompt_survives_with_the_reply_it_follows():
    messages = [Message.user("start")]
    for i in range(10):
        messages += _turn(f"c{i}", "read_file", {"file": f"f{i}.py"}, "x" * 20_000)
    messages.append(Message.assistant("Option 1 or option 2?"))
    messages.append(Message.user("do option 2"))

    result, state = compact_context(
        messages, lambda prompt: "## Goal\ng",
        context_length=CONTEXT_LENGTH, chars_per_token=CHARS_PER_TOKEN,
    )

    assert [m.text for m in result[1:]] == ["Option 1 or option 2?", "do option 2"]
    # Still a live message, so not duplicated in the carried requests.
    assert state.user_messages == ()


def test_later_user_messages_are_carried_verbatim_across_compactions():
    messages = [Message.user("build it")]
    messages += _turn("c0", "read_file", {"file": "a.py"}, "x")
    messages.append(Message.assistant("ok"))
    messages.append(Message.user("no, keep the old API\nand add tests", display_content=None))
    messages += _turn("c1", "read_file", {"file": "b.py"}, "x")
    messages.append(Message.user([], display_content=""))  # hidden payload
    messages.append(Message.inbox("process 3 exited"))
    messages.append(Message.assistant("done"))

    first, state = compact_context(messages, lambda prompt: "## Goal\ng")
    assert state.original_request == "build it"
    assert state.user_messages == ("no, keep the old API\nand add tests",)
    assert "> no, keep the old API\n> and add tests" in first[0].text

    second_input = first + [Message.user("ship it")] + _turn(
        "c2", "read_file", {"file": "c.py"}, "x"
    ) + [Message.assistant("shipped")]
    second, state = compact_context(second_input, lambda prompt: "## Goal\ng", state=state)
    assert state.user_messages == ("no, keep the old API\nand add tests", "ship it")
    # Carried messages are rebuilt from state, never re-summarized.
    assert second[0].text.count("keep the old API") == 1


def test_carried_user_messages_keep_the_newest_within_budget():
    state = CompactionState()
    for i in range(20):
        state = state.absorb([
            Message.user(f"M{i} " + "z" * 3_000),
            Message.assistant("ok"),
        ])
    assert state.original_request.startswith("M0 ")
    assert state.user_messages[-1].startswith("M19 ")
    assert sum(len(text) for text in state.user_messages) <= 32_000
    assert state.omitted_user_messages == 19 - len(state.user_messages)
    assert "older message(s) omitted" in "\n".join(state.render_requests())


def test_transcript_is_saved_verbatim_and_referenced():
    messages = [Message.user("start")]
    messages += _turn("c0", "read_file", {"file": "a.py"}, "FULL " + "q" * 5_000)
    messages.append(Message.assistant("done"))
    saved = []

    def save(text):
        saved.append(text)
        return f".ene/tool-results/s/t{len(saved)}.txt"

    first, state = compact_context(messages, lambda prompt: "## Goal\ng", save_transcript=save)
    # The transcript has what the summarizer only saw an excerpt of.
    assert "FULL " + "q" * 5_000 in saved[0]
    assert "Tool result: read_file (c0)" in saved[0]
    assert ".ene/tool-results/s/t1.txt" in first[0].text
    assert state.transcripts == (".ene/tool-results/s/t1.txt",)

    second_input = first + _turn("c1", "read_file", {"file": "b.py"}, "x") + [
        Message.assistant("again")
    ]
    second, state = compact_context(
        second_input, lambda prompt: "## Goal\ng", state=state, save_transcript=save
    )
    assert state.transcripts == (".ene/tool-results/s/t1.txt", ".ene/tool-results/s/t2.txt")
    assert "t1.txt, .ene/tool-results/s/t2.txt" in second[0].text


def test_failed_transcript_save_still_compacts():
    messages = [Message.user("start")] + _turn("c0", "ls", {"path": "."}, "x") + [
        Message.assistant("done")
    ]

    def fail(_text):
        raise OSError("disk full")

    result, state = compact_context(messages, lambda prompt: "## Goal\ng", save_transcript=fail)
    assert result is not messages
    assert state.transcripts == ()
    assert _TRANSCRIPT_HEADING not in result[0].text


def test_update_uses_the_carried_summary_not_the_whole_handoff():
    messages = [Message.user("build it")] + _turn("c0", "ls", {"path": "."}, "x") + [
        Message.assistant("done")
    ]
    first, state = compact_context(messages, lambda prompt: "## Goal\nFIRST-SUMMARY")

    seen, summarize = _capture()
    compact_context(
        first + _turn("c1", "ls", {"path": "src"}, "x") + [Message.assistant("more")],
        summarize, state=state,
    )
    previous = seen["prompt"].split("<previous-summary>")[1].split("</previous-summary>")[0]
    assert previous.strip() == "## Goal\nFIRST-SUMMARY"
    # Tool calls reach the summarizer with what they acted on.
    assert "[Called tool: ls(src)]" in seen["prompt"]


def test_manual_compaction_of_a_short_session_spends_nothing():
    """/compact skips the trigger, but must not rewrite one message for a round-trip."""
    messages = [Message.user("add a retry to the uploader")]
    for i in range(12):
        messages += _turn(f"c{i}", "read_file", {"file": f"f{i}.py"}, "x" * 1_500)
    messages.append(Message.assistant("done"))
    used = sum(m.chars for m in messages) // 4
    calls = []

    result, _ = compact_context(
        messages, lambda prompt: calls.append(prompt) or "## Goal\ng",
        context_length=CONTEXT_LENGTH, chars_per_token=CHARS_PER_TOKEN,
    )

    # The whole history sits inside the protected recent window.
    assert not calls
    assert result is messages


def test_compaction_is_a_no_op_when_only_the_in_flight_round_has_work():
    """Only user messages precede the round in flight: nothing to summarize."""
    messages = [
        Message.user("hello"),
        Message.user("and also this"),
        Message.assistant(
            content="",
            tool_calls=[ToolCall("c0", "read_file", '{"file": "a.py"}')],
        ),
        Message.tool("c0", "x" * 500),
    ]
    calls = []

    result, _ = compact_context(messages, lambda prompt: calls.append(prompt) or "S")

    assert not calls  # no round-trip spent on an empty conversation
    assert result is messages


def test_prior_summary_is_not_clipped_when_compacting_again():
    """Repeated compaction must not lose the early session a summary stands for."""
    prior = f"{SUMMARY_MARKER}\n" + "PRIOR-" * 1000
    messages = [
        Message.user(prior),
        Message.assistant("ok"),
        Message.user("next"),
        Message.assistant("done"),
    ]
    seen = {}

    def summarize(prompt):
        seen["prompt"] = prompt
        return "new summary"

    result, _ = compact_context(messages, summarize)

    assert seen["prompt"].count("PRIOR-") == 1000
    assert result[0].text.startswith(SUMMARY_MARKER)


def test_a_reply_being_continued_survives_whatever_its_size():
    """A response cut off by the output limit is continued from its own text."""
    messages = [Message.user("write the report")]
    for i in range(10):
        messages += _turn(f"c{i}", "read_file", {"file": f"f{i}.py"}, "x" * 20_000)
    partial = "PARTIAL " + "r" * 30_000
    messages.append(Message.assistant(partial))

    result, _ = compact_context(
        messages, lambda prompt: "## Goal\ng",
        context_length=CONTEXT_LENGTH, chars_per_token=CHARS_PER_TOKEN,
    )

    assert [m.text for m in result[1:]] == [partial]


def test_a_long_reply_followed_by_a_prompt_is_summarized():
    messages = [Message.user("write the report")]
    for i in range(10):
        messages += _turn(f"c{i}", "read_file", {"file": f"f{i}.py"}, "x" * 20_000)
    messages.append(Message.assistant("LONG " + "r" * 30_000))
    messages.append(Message.user("shorten it"))

    seen, summarize = _capture()
    result, _ = compact_context(
        messages, summarize,
        context_length=CONTEXT_LENGTH, chars_per_token=CHARS_PER_TOKEN,
    )

    assert [m.text for m in result[1:]] == ["shorten it"]
    assert "LONG " in seen["prompt"]
