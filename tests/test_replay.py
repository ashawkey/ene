from ene.replay import HiddenMessages, compact_replay, hidden_message


def _select(items):
    return compact_replay(
        items,
        is_user=lambda item: item[0] == "user",
        is_assistant=lambda item: item[0] == "assistant",
        has_text=lambda item: bool(item[1].strip()),
    )


def test_hidden_message_pluralizes_count():
    assert hidden_message(1) == "1 message hidden"
    assert hidden_message(2) == "2 messages hidden"


def test_compact_replay_keeps_all_turns_and_marks_omissions_in_order():
    items = []
    for turn in range(12):
        items.extend([
            ("user", f"prompt {turn}"),
            ("assistant", f"intermediate {turn}"),
            ("tool", "large result"),
            ("assistant", f"final {turn}"),
        ])

    replay = _select(items)

    assert replay[:3] == [
        ("user", "prompt 0"),
        HiddenMessages(2),
        ("assistant", "final 0"),
    ]
    assert replay[-1] == ("assistant", "final 11")
    assert len(replay) == 36


def test_compact_replay_keeps_unfinished_latest_prompt():
    assert _select([
        ("user", "done"),
        ("assistant", "answer"),
        ("user", "still working"),
        ("assistant", ""),
    ]) == [
        ("user", "done"),
        ("assistant", "answer"),
        ("user", "still working"),
        HiddenMessages(1),
    ]


def test_compact_replay_preserves_last_reply_when_history_starts_mid_turn():
    items = [
        ("tool", "old output"),
        ("assistant", "intermediate"),
        ("tool", "recent output"),
        ("assistant", "final answer"),
    ]

    replay = compact_replay(
        items,
        is_user=lambda item: item[0] == "user",
        is_assistant=lambda item: item[0] == "assistant",
        has_text=lambda item: bool(item[1]),
        starts_mid_turn=True,
    )

    assert replay == [HiddenMessages(3), ("assistant", "final answer")]


def test_compact_replay_can_require_a_turn_start_marker():
    items = [
        ("user", "/usage"),
        ("system", "usage"),
        ("user", "real prompt"),
        ("start", ""),
        ("assistant", "answer"),
    ]

    replay = compact_replay(
        items,
        is_user=lambda item: item[0] == "user",
        is_assistant=lambda item: item[0] == "assistant",
        has_text=lambda item: bool(item[1]),
        is_turn_start=lambda item: item[0] == "start",
    )

    assert replay == [
        HiddenMessages(2),
        ("user", "real prompt"),
        HiddenMessages(1),
        ("assistant", "answer"),
    ]


def test_compact_replay_only_counts_visible_omissions():
    items = [
        ("user", "question"),
        ("internal", "iteration"),
        ("tool", "output"),
        ("assistant", "answer"),
    ]

    replay = compact_replay(
        items,
        is_user=lambda item: item[0] == "user",
        is_assistant=lambda item: item[0] == "assistant",
        has_text=lambda item: bool(item[1]),
        is_visible=lambda item: item[0] != "internal",
    )

    assert replay == [
        ("user", "question"),
        HiddenMessages(1),
        ("assistant", "answer"),
    ]
