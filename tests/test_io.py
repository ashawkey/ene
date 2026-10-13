import asyncio
import json
import queue
import threading
import time
from pathlib import Path

import pytest

from ene.utils.io import (
    EVENT_TEXT_LIMIT,
    INBOX_EVENT_TEXT_LIMIT,
    INBOX_MAX_EVENTS,
    INBOX_MAX_EVENTS_PER_SOURCE,
    CancellationToken,
    EventHub,
    InputBroker,
    PromptBroker,
    parse_wait_command,
    sanitize_unicode,
)
from ene.utils.interrupt import RequestInterrupted, run_interruptible
from ene.ui import AgentConsole


def test_event_hub_orders_and_bounds_replay():
    hub = EventHub(max_events=2)
    hub.publish("system", text="one")
    second = hub.publish("system", text="two")
    third = hub.publish("system", text="three")

    assert [event.seq for event in hub.after(0)] == [second.seq, third.seq]
    assert hub.latest_seq == third.seq
    assert hub.oldest_seq == second.seq
    assert hub.has_replay_gap(0)
    assert not hub.has_replay_gap(second.seq - 1)


def test_input_broker_appends_follow_ups_to_pending_submission():
    hub = EventHub()
    broker = InputBroker(hub)
    first = broker.submit("first", source="terminal")
    merged = broker.submit("second", source="web", action_id="a2")

    assert merged.text == "first\n\nsecond"
    assert merged.id != first.id
    assert merged.source == "web" and merged.action_id == "a2"
    with pytest.raises(queue.Empty):
        broker.get_nowait(first.id)  # a stale ID cannot take the longer text
    assert broker.get_nowait(merged.id) == merged
    assert [(e.type, e.data.get("reason")) for e in hub.after(0)] == [
        ("pending_set", None),
        ("pending_cleared", "merged"),
        ("pending_set", None),
        ("pending_cleared", "consumed"),
    ]


@pytest.mark.parametrize(
    "pending, follow_up",
    [
        ("first", "/compact"),
        ("first", "!ls"),
        ("first", "exit"),
        ("/compact", "second"),
        ("first", "/wait 5m later"),
        ("/wait 5m later", "second"),
    ],
)
def test_input_broker_never_combines_commands_or_delayed_messages(pending, follow_up):
    broker = InputBroker(EventHub())
    existing = broker.submit(pending, source="terminal")

    assert not broker.accepts_while_pending(follow_up)
    with pytest.raises(ValueError, match="already pending"):
        broker.submit(follow_up, source="terminal")
    assert broker.submission == existing


def test_accepts_while_pending_hints_plain_follow_ups():
    broker = InputBroker(EventHub())
    assert not broker.accepts_while_pending("hello")  # nothing pending
    broker.submit("first")
    assert broker.accepts_while_pending("  more detail ")


def test_input_broker_rejects_oversized_combined_message():
    broker = InputBroker(EventHub())
    broker.submit("a" * (EVENT_TEXT_LIMIT - 10))
    with pytest.raises(ValueError, match="too large"):
        broker.submit("b" * 20)
    assert broker.submission.text == "a" * (EVENT_TEXT_LIMIT - 10)


@pytest.mark.parametrize(
    "text, seconds, prompt",
    [
        ("/wait 30s check status", 30, "check status"),
        ("/WAIT 2min do it", 120, "do it"),
        ("/wait 1.5hours finish task", 5400, "finish task"),
    ],
)
def test_parse_wait_command(text, seconds, prompt):
    assert parse_wait_command(text) == (seconds, prompt)


@pytest.mark.parametrize("text", ["/wait", "/wait 1d prompt", "/wait 0s prompt"])
def test_parse_wait_command_rejects_invalid_input(text):
    with pytest.raises(ValueError):
        parse_wait_command(text)


def test_wait_submission_is_hidden_until_ready_and_is_non_steering():
    hub = EventHub()
    broker = InputBroker(hub)
    ready = threading.Event()
    broker.add_listener(ready.set)

    submission = broker.submit("/wait 0.05s check status", source="web")
    assert submission.text == "check status"
    assert not submission.steer
    assert not submission.ready
    assert not broker.ready
    with pytest.raises(queue.Empty):
        broker.get_nowait()

    # Poll broker state rather than the event flag: the immediate pending-set
    # notification can race with clear() under load.
    deadline = time.time() + 2
    while not broker.ready and time.time() < deadline:
        time.sleep(0.01)
    assert broker.ready
    assert broker.get_nowait() == submission


def test_withdrawing_wait_submission_cancels_ready_notification():
    broker = InputBroker(EventHub())
    notified = threading.Event()
    broker.add_listener(notified.set)
    submission = broker.submit("/wait 0.05s later", source="terminal")
    notified.clear()

    assert broker.withdraw() == submission
    notified.clear()  # discard the withdrawal notification
    assert not notified.wait(0.1)


def test_input_broker_consumes_only_the_expected_submission():
    hub = EventHub()
    broker = InputBroker(hub)
    first = broker.submit("first")

    with pytest.raises(queue.Empty):
        broker.get_nowait("different-id")
    assert broker.submission == first
    assert broker.get_nowait(first.id) == first
    assert broker.submission is None
    assert [(e.type, e.data.get("reason")) for e in hub.after(0)] == [
        ("pending_set", None),
        ("pending_cleared", "consumed"),
    ]


def test_inbox_queues_notifications_beside_pending_submission():
    broker = InputBroker(EventHub())
    wakes = []
    broker.add_listener(lambda: wakes.append(True))
    submission = broker.submit("hello", source="terminal")
    first = broker.post_event("job done", "process:1")
    broker.post_event("new message", "slack")

    assert broker.submission == submission
    assert broker.inbox_size == 2 and broker.has_work
    assert len(wakes) == 3
    events, dropped = broker.drain_events()
    assert [event.text for event in events] == ["job done", "new message"]
    assert events[0] == first and dropped == {}
    assert broker.inbox_size == 0
    assert broker.has_work  # the user submission is still ready


def test_inbox_waits_for_delayed_submission_but_not_for_events(monkeypatch):
    monkeypatch.setattr("ene.utils.io.INBOX_BATCH_SECONDS", 0)
    broker = InputBroker(EventHub())
    broker.submit("/wait 1h later", source="terminal")
    assert not broker.has_work
    broker.post_event("ping", "external")
    assert broker.has_work


def test_inbox_batches_notifications_before_waking_an_idle_agent(monkeypatch):
    monkeypatch.setattr("ene.utils.io.INBOX_BATCH_SECONDS", 0.2)
    broker = InputBroker(EventHub())
    woken = threading.Event()
    broker.add_listener(woken.set)
    broker.post_event("line 1", "process:1")
    broker.post_event("line 2", "process:1")
    assert broker.inbox_size == 2 and not broker.has_work
    woken.clear()
    # The window's end wakes listeners again, now with work to do.
    assert woken.wait(5)
    assert broker.has_work and broker.inbox_ready
    events, _ = broker.drain_events()
    assert [event.text for event in events] == ["line 1", "line 2"]
    assert not broker.has_work
    # Each new burst opens a fresh window.
    broker.post_event("line 3", "process:1")
    assert not broker.has_work


def test_cleared_inbox_window_does_not_mark_later_events_ready(monkeypatch):
    monkeypatch.setattr("ene.utils.io.INBOX_BATCH_SECONDS", 0.2)
    broker = InputBroker(EventHub())
    broker.post_event("stale", "process:1")
    broker.clear_events()
    time.sleep(0.3)
    broker.post_event("fresh", "process:2")
    assert not broker.has_work


def test_pending_messages_report_whether_follow_ups_can_append():
    hub = EventHub()
    broker = InputBroker(hub)
    broker.submit("first", source="terminal")
    assert hub.after(0)[-1].data["appendable"] is True
    assert broker.accepts_while_pending("follow up")
    assert not broker.accepts_while_pending("/compact")
    broker.withdraw()
    broker.submit("/wait 1h later", source="terminal")
    assert hub.after(0)[-1].data["appendable"] is False
    assert not broker.accepts_while_pending("follow up")


def test_inbox_bounds_each_source_and_reports_drops():
    broker = InputBroker(EventHub())
    for index in range(INBOX_MAX_EVENTS_PER_SOURCE + 2):
        broker.post_event(f"line {index}", "process:1")
    broker.post_event("other", "slack")

    events, dropped = broker.drain_events(max_chars=10**9)
    texts = [event.text for event in events]
    assert texts[0] == "line 2" and texts[-1] == "other"
    assert dropped == {"process:1": 2}
    assert broker.drain_events() == ([], {})


def test_inbox_bounds_total_size():
    broker = InputBroker(EventHub())
    for index in range(INBOX_MAX_EVENTS + 1):
        broker.post_event("x", f"source:{index}")
    assert broker.inbox_size == INBOX_MAX_EVENTS
    _events, dropped = broker.drain_events(max_chars=10**9)
    assert dropped == {"source:0": 1}


def test_inbox_drains_by_size_and_clips_large_events():
    broker = InputBroker(EventHub())
    broker.post_event("a" * (INBOX_EVENT_TEXT_LIMIT * 2), "big")
    broker.post_event("b" * 10, "small")

    events, _ = broker.drain_events(max_chars=100)
    assert len(events) == 1  # the first event always drains
    assert len(events[0].text) < INBOX_EVENT_TEXT_LIMIT + 100
    assert broker.inbox_size == 1
    assert broker.clear_events() == 1 and broker.inbox_size == 0


def test_inbox_overflow_metadata_is_bounded_and_budgeted(monkeypatch):
    from ene.utils.io import INBOX_MAX_DROPPED_SOURCES, INBOX_OTHER_SOURCES

    monkeypatch.setattr("ene.utils.io.INBOX_BATCH_SECONDS", 0)
    broker = InputBroker(EventHub())
    for index in range(10000):
        broker.post_event("x" * 100, f"{index}:" + "&" * 70)
    assert len(broker._inbox_dropped) <= INBOX_MAX_DROPPED_SOURCES + 1
    events, dropped = broker.drain_events(max_chars=1024)
    assert dropped == {INBOX_OTHER_SOURCES: 10000 - INBOX_MAX_EVENTS}
    notice_budget = sum(100 + 6 * len(source) + len(str(count)) for source, count in dropped.items())
    assert sum(len(event.text) for event in events) + notice_budget <= 1024
    assert broker.inbox_size > 0 and broker.has_work
    assert broker.drain_events()[1] == {}


@pytest.mark.parametrize("text", ["", "   "])
def test_inbox_rejects_empty_events(text):
    with pytest.raises(ValueError):
        InputBroker(EventHub()).post_event(text, "external")


def test_prompt_broker_first_valid_answer_wins():
    hub = EventHub()
    broker = PromptBroker(hub)
    result = []

    thread = threading.Thread(
        target=lambda: result.append(
            broker.ask("select", "Continue?", choices=["Yes", "No"])
        )
    )
    thread.start()
    deadline = time.time() + 2
    while broker.active is None and time.time() < deadline:
        time.sleep(0.01)

    prompt = broker.active
    assert prompt is not None
    assert not broker.resolve(prompt.id, "invalid")
    assert broker.resolve(prompt.id, "Yes")
    assert not broker.resolve(prompt.id, "No")
    thread.join(timeout=2)
    assert result == ["Yes"]


def test_web_answer_cancels_pending_terminal_adapter():
    hub = EventHub()
    broker = PromptBroker(hub)
    terminal_started = threading.Event()
    terminal_cancelled = threading.Event()
    result = []

    async def wait_on_terminal(_prompt):
        terminal_started.set()
        try:
            await asyncio.sleep(5)
        finally:
            terminal_cancelled.set()

    broker.set_terminal_adapter(wait_on_terminal)
    thread = threading.Thread(
        target=lambda: result.append(
            broker.ask("select", "Continue?", choices=["Yes", "No"])
        )
    )
    thread.start()
    assert terminal_started.wait(timeout=2)

    prompt = broker.active
    assert prompt is not None
    assert broker.resolve(prompt.id, "No", source="web")
    thread.join(timeout=2)
    assert result == ["No"]
    assert terminal_cancelled.is_set()


def test_cancellation_releases_active_prompt():
    events = EventHub()
    prompts = PromptBroker(events)
    cancellation = CancellationToken(events, prompts)
    operation_id = cancellation.begin("tool")
    result = []
    thread = threading.Thread(
        target=lambda: result.append(prompts.ask("confirm", "Run tool?"))
    )
    thread.start()
    for _ in range(100):
        if prompts.active is not None:
            break
        time.sleep(0.01)

    assert cancellation.cancel(operation_id)
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert result == [None]
