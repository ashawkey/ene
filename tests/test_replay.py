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


def test_compact_replay_keeps_every_direct_user_and_assistant_message():
    items = [
        ("user", "prompt"),
        ("assistant", "intermediate"),
        ("tool", "large result"),
        ("assistant", "final"),
        ("user", "/usage"),
        ("system", "usage"),
    ]

    assert _select(items) == [
        ("user", "prompt"),
        ("assistant", "intermediate"),
        HiddenMessages(1),
        ("assistant", "final"),
        ("user", "/usage"),
        HiddenMessages(1),
    ]


def test_compact_replay_folds_empty_assistant_records():
    assert _select([
        ("user", "still working"),
        ("assistant", ""),
        ("tool", "output"),
    ]) == [
        ("user", "still working"),
        HiddenMessages(2),
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
