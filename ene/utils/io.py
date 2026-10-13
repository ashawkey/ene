"""Thread-safe shared I/O primitives for terminal and web clients."""

from __future__ import annotations

import asyncio
import math
import queue
import re
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable


# Cap per-field payload of published events so the EventHub's replay
# history stays bounded even for large file writes or tool outputs.
EVENT_TEXT_LIMIT = 64 * 1024
# Bounds for asynchronous inbox notifications. A noisy source loses its oldest
# events first, so one chatty process cannot crowd out every other source.
INBOX_MAX_EVENTS = 200
INBOX_MAX_EVENTS_PER_SOURCE = 50
INBOX_EVENT_TEXT_LIMIT = 8 * 1024
INBOX_SOURCE_LIMIT = 80
INBOX_MAX_DROPPED_SOURCES = 32
INBOX_OTHER_SOURCES = "[other sources]"
# Notifications that wake an idle agent wait this long for companions, so a
# chatty source starts one round per burst instead of one per line.
INBOX_BATCH_SECONDS = 0.5
_WAIT_UNITS = {
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
}
_WAIT_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)([a-z]+)$", re.IGNORECASE)


def parse_wait_command(text: str) -> tuple[float, str] | None:
    """Parse ``/wait <duration> <prompt>`` or return ``None`` for other input."""
    if not re.match(r"^/wait(?:\s|$)", text, re.IGNORECASE):
        return None
    parts = text.split(maxsplit=2)
    if len(parts) != 3:
        raise ValueError(
            "Usage: /wait <duration> <prompt> "
            "(for example, /wait 1h check the job)"
        )
    match = _WAIT_DURATION_RE.fullmatch(parts[1])
    unit = match.group(2).lower() if match else ""
    if match is None or unit not in _WAIT_UNITS:
        raise ValueError(
            "Invalid wait duration. Use seconds, minutes, or hours "
            "(for example: 30s, 5m, 1h)."
        )
    delay = float(match.group(1)) * _WAIT_UNITS[unit]
    if not math.isfinite(delay) or delay <= 0:
        raise ValueError("Wait duration must be greater than zero.")
    prompt = parts[2].strip()
    if not prompt:
        raise ValueError("A prompt is required after the wait duration.")
    return delay, prompt


def is_local_query(text: str) -> bool:
    """Whether input is handled locally (command, shell, exit), not by the model."""
    return text.lower() in ("exit", "quit") or text.startswith(("!", "/"))


def sanitize_unicode(value: Any) -> Any:
    """Replace invalid surrogate code points in JSON-like external data."""
    if isinstance(value, str):
        if not any("\ud800" <= char <= "\udfff" for char in value):
            return value
        return value.encode("utf-16", errors="surrogatepass").decode(
            "utf-16", errors="replace"
        )
    if isinstance(value, list):
        return [sanitize_unicode(item) for item in value]
    if isinstance(value, dict):
        return {
            sanitize_unicode(key): sanitize_unicode(item)
            for key, item in value.items()
        }
    return value


def _clip_text(text: str, limit: int = EVENT_TEXT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... (truncated, {len(text) - limit} more characters)"


@dataclass(frozen=True)
class AgentEvent:
    seq: int
    type: str
    data: dict[str, Any]
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EventHub:
    """Publish ordered events and retain a bounded reconnect history."""

    def __init__(self, max_events: int = 2000):
        self.stream_id = uuid.uuid4().hex
        self._events: deque[AgentEvent] = deque(maxlen=max_events)
        self._cond = threading.Condition()
        self._seq = 0
        self._listeners: set[Callable[[AgentEvent], None]] = set()

    def add_listener(self, listener: Callable[[AgentEvent], None]) -> None:
        with self._cond:
            self._listeners.add(listener)

    def remove_listener(self, listener: Callable[[AgentEvent], None]) -> None:
        with self._cond:
            self._listeners.discard(listener)

    def publish(self, event_type: str, **data: Any) -> AgentEvent:
        data = sanitize_unicode(data)
        data = {
            key: _clip_text(value) if isinstance(value, str) else value
            for key, value in data.items()
        }
        with self._cond:
            self._seq += 1
            event = AgentEvent(self._seq, event_type, data, time.time())
            self._events.append(event)
            self._cond.notify_all()
            # Keep delivery serialized with sequence assignment. Attach
            # forwarders use sequence numbers for replay de-duplication, so a
            # later event must never reach them before an earlier one.
            for listener in tuple(self._listeners):
                try:
                    listener(event)
                except Exception:
                    # Event observers are best-effort side channels. A broken
                    # journal, client, or status sink must not abort agent work.
                    continue
            return event

    def snapshot(self) -> list[AgentEvent]:
        """Return the currently retained bounded event history."""
        with self._cond:
            return list(self._events)

    def after(self, seq: int) -> list[AgentEvent]:
        with self._cond:
            return [event for event in self._events if event.seq > seq]

    def wait_after(self, seq: int, timeout: float = 1.0) -> list[AgentEvent]:
        """Block until events newer than *seq* exist (or *timeout*), then return them."""
        with self._cond:
            self._cond.wait_for(lambda: self._seq > seq, timeout=timeout)
            return [event for event in self._events if event.seq > seq]

    @property
    def latest_seq(self) -> int:
        with self._cond:
            return self._seq

    @property
    def oldest_seq(self) -> int:
        """Sequence number of the oldest retained event (or the next event)."""
        with self._cond:
            return self._events[0].seq if self._events else self._seq + 1

    def has_replay_gap(self, after_seq: int) -> bool:
        """Whether events after *after_seq* were evicted from bounded history."""
        with self._cond:
            return bool(self._events and after_seq < self._events[0].seq - 1)


@dataclass(frozen=True)
class UserSubmission:
    text: str
    source: str
    id: str
    action_id: str | None = None
    ready_at: float | None = None
    steer: bool = True

    @property
    def ready(self) -> bool:
        return self.ready_at is None or time.monotonic() >= self.ready_at

    @property
    def appendable(self) -> bool:
        """Whether plain follow-up messages may join this one while it is pending."""
        return self.ready_at is None and not is_local_query(self.text)



@dataclass(frozen=True)
class InboxEvent:
    """One asynchronous notification waiting to wake the agent."""

    text: str
    source: str
    id: str
    created_at: float


class InputBroker:
    """User submissions and asynchronous notifications that wake the agent.

    Terminal and web clients share a single editable pending submission;
    plain follow-up messages are appended to it.
    Notifications from processes, timers, and external clients queue
    separately in a bounded FIFO inbox and are delivered in batches.
    """

    def __init__(self, events: EventHub):
        self.events = events
        self._lock = threading.Lock()
        self._submission: UserSubmission | None = None
        self._inbox: deque[InboxEvent] = deque()
        self._inbox_counts: dict[str, int] = {}
        self._inbox_dropped: dict[str, int] = {}
        # Set once queued notifications have waited out the batch window.
        self._inbox_batched = False
        self._inbox_timer: threading.Timer | None = None
        # Identifies the current window, so a stale timer cannot close a new one.
        self._inbox_window = 0
        self._listeners: set[Callable[[], None]] = set()
        self._ready_timer: threading.Timer | None = None

    def add_listener(self, listener: Callable[[], None]) -> None:
        with self._lock:
            self._listeners.add(listener)

    def remove_listener(self, listener: Callable[[], None]) -> None:
        with self._lock:
            self._listeners.discard(listener)

    def _notify(self, listeners: tuple[Callable[[], None], ...]) -> None:
        for listener in listeners:
            listener()

    def _notify_ready(self, submission_id: str) -> None:
        with self._lock:
            if self._submission is None or self._submission.id != submission_id:
                return
            self._ready_timer = None
            listeners = tuple(self._listeners)
        self._notify(listeners)

    def submit(
        self,
        text: str,
        source: str = "web",
        action_id: str | None = None,
        *,
        delay: float = 0,
        steer: bool = True,
    ) -> UserSubmission:
        text = sanitize_unicode(text).strip()
        if not text:
            raise ValueError("Message cannot be empty.")
        wait = parse_wait_command(text) if source in {"terminal", "web"} else None
        if wait is not None:
            delay, text = wait
            steer = False
        if len(text.encode("utf-8")) > EVENT_TEXT_LIMIT:
            raise ValueError("Message is too large.")
        if not math.isfinite(delay) or delay < 0:
            raise ValueError("Delay must be a non-negative number of seconds.")
        item = UserSubmission(
            text=text,
            source=source,
            id=uuid.uuid4().hex,
            action_id=action_id,
            ready_at=time.monotonic() + delay if delay else None,
            steer=steer,
        )
        with self._lock:
            existing = self._submission
            if existing is not None:
                # Plain follow-ups append to the pending message, so a user can
                # keep typing while a round runs. The merged message gets a new
                # ID: a consumer holding the old ID must not take the longer
                # text while acting on the shorter one.
                if not self.can_append(existing, item):
                    raise ValueError(
                        "Another message is already pending; commands and /wait "
                        "messages cannot be combined with it."
                    )
                merged = f"{existing.text}\n\n{item.text}"
                if len(merged.encode("utf-8")) > EVENT_TEXT_LIMIT:
                    raise ValueError("Combined pending message is too large.")
                item = UserSubmission(
                    text=merged,
                    source=item.source,
                    id=item.id,
                    action_id=action_id,
                    steer=existing.steer and item.steer,
                )
                self.events.publish(
                    "pending_cleared", id=existing.id, reason="merged"
                )
            self._submission = item
            self.events.publish(
                "pending_set",
                id=item.id,
                text=item.text,
                source=item.source,
                action_id=item.action_id,
                delay=delay,
                steer=item.steer,
                appendable=item.appendable,
            )
            listeners = tuple(self._listeners)
            if delay:
                timer = threading.Timer(delay, self._notify_ready, args=(item.id,))
                timer.daemon = True
                self._ready_timer = timer
                timer.start()
        self._notify(listeners)
        return item

    @staticmethod
    def can_append(existing: UserSubmission, item: UserSubmission) -> bool:
        """Whether *item* may be appended to the pending *existing* message."""
        return existing.appendable and item.appendable

    def accepts_while_pending(self, text: str) -> bool:
        """Client-side hint: whether *text* could join the current pending message."""
        with self._lock:
            existing = self._submission
        return (
            existing is not None
            and existing.appendable
            and not is_local_query(text.strip())
        )

    def get_nowait(self, submission_id: str | None = None) -> UserSubmission:
        """Consume the pending submission, optionally only when its ID matches."""
        with self._lock:
            item = self._submission
            if item is None or not item.ready or (
                submission_id is not None and item.id != submission_id
            ):
                raise queue.Empty
            self._submission = None
            if self._ready_timer is not None:
                self._ready_timer.cancel()
                self._ready_timer = None
            self.events.publish("pending_cleared", id=item.id, reason="consumed")
            listeners = tuple(self._listeners)
        self._notify(listeners)
        return item

    def withdraw(
        self,
        submission_id: str | None = None,
        action_id: str | None = None,
    ) -> UserSubmission | None:
        """Remove and return the pending message for editing."""
        with self._lock:
            item = self._submission
            if item is None or (
                submission_id is not None and item.id != submission_id
            ):
                return None
            self._submission = None
            if self._ready_timer is not None:
                self._ready_timer.cancel()
                self._ready_timer = None
            self.events.publish(
                "pending_cleared",
                id=item.id,
                reason="withdrawn",
                action_id=action_id,
                text=item.text,
            )
            listeners = tuple(self._listeners)
        self._notify(listeners)
        return item

    @property
    def submission(self) -> UserSubmission | None:
        with self._lock:
            return self._submission

    @property
    def pending(self) -> int:
        with self._lock:
            return int(self._submission is not None)

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._submission is not None and self._submission.ready

    def post_event(self, text: str, source: str) -> InboxEvent:
        """Queue a notification and wake listeners; never blocks the producer."""
        text = sanitize_unicode(str(text)).strip()
        if not text:
            raise ValueError("Notification cannot be empty.")
        text = _clip_text(text, INBOX_EVENT_TEXT_LIMIT)
        source = " ".join(sanitize_unicode(str(source)).split())[:INBOX_SOURCE_LIMIT]
        item = InboxEvent(
            text=text,
            source=source or "external",
            id=uuid.uuid4().hex,
            created_at=time.time(),
        )
        with self._lock:
            if self._inbox_counts.get(item.source, 0) >= INBOX_MAX_EVENTS_PER_SOURCE:
                dropped = next(e for e in self._inbox if e.source == item.source)
                self._inbox.remove(dropped)
            elif len(self._inbox) >= INBOX_MAX_EVENTS:
                dropped = self._inbox.popleft()
            else:
                dropped = None
            if dropped is not None:
                self._count_removed(dropped)
                source = dropped.source
                if (source not in self._inbox_dropped
                        and len(self._inbox_dropped) >= INBOX_MAX_DROPPED_SOURCES):
                    source = INBOX_OTHER_SOURCES
                self._inbox_dropped[source] = (
                    self._inbox_dropped.get(source, 0) + 1
                )
            self._inbox.append(item)
            self._inbox_counts[item.source] = self._inbox_counts.get(item.source, 0) + 1
            if INBOX_BATCH_SECONDS <= 0:
                self._inbox_batched = True
            elif not self._inbox_batched and self._inbox_timer is None:
                # Wake listeners again once the batch window has passed.
                self._inbox_window += 1
                timer = threading.Timer(
                    INBOX_BATCH_SECONDS, self._notify_inbox_ready, args=(self._inbox_window,)
                )
                timer.daemon = True
                self._inbox_timer = timer
                timer.start()
            listeners = tuple(self._listeners)
        self._notify(listeners)
        return item

    def _count_removed(self, event: InboxEvent) -> None:
        remaining = self._inbox_counts[event.source] - 1
        if remaining:
            self._inbox_counts[event.source] = remaining
        else:
            del self._inbox_counts[event.source]

    def _reset_inbox_window_locked(self) -> None:
        self._inbox_batched = False
        self._inbox_window += 1
        if self._inbox_timer is not None:
            self._inbox_timer.cancel()
            self._inbox_timer = None

    def _notify_inbox_ready(self, window: int) -> None:
        with self._lock:
            if window != self._inbox_window:
                return
            self._inbox_timer = None
            self._inbox_batched = bool(self._inbox)
            listeners = tuple(self._listeners) if self._inbox else ()
        self._notify(listeners)

    def drain_events(
        self, max_chars: int = 4 * INBOX_EVENT_TEXT_LIMIT
    ) -> tuple[list[InboxEvent], dict[str, int]]:
        """Consume queued notifications in order, up to roughly *max_chars*.

        At least one event is returned when any is queued; the remainder stays
        queued for the next delivery. Also returns and resets per-source
        counts of events dropped because the inbox overflowed.
        """
        with self._lock:
            events: list[InboxEvent] = []
            dropped, self._inbox_dropped = self._inbox_dropped, {}
            # Reserve space for overflow reporting too. Source labels can
            # expand sixfold when HTML-escaped by the inbox renderer.
            def notice_size(source: str, count: int) -> int:
                return 100 + 6 * len(source) + len(str(count))

            budget = max(256, max_chars // 4)
            used = sum(notice_size(source, count) for source, count in dropped.items())
            if used > budget:
                total = sum(dropped.values())
                dropped = {INBOX_OTHER_SOURCES: total}
                used = notice_size(INBOX_OTHER_SOURCES, total)
            while self._inbox:
                size = len(self._inbox[0].text)
                if events and used + size > max_chars:
                    break
                event = self._inbox.popleft()
                self._count_removed(event)
                events.append(event)
                used += size
            if not self._inbox:
                self._reset_inbox_window_locked()
            listeners = tuple(self._listeners) if events else ()
        self._notify(listeners)
        return events, dropped

    def clear_events(self) -> int:
        """Discard every queued notification; return how many were dropped."""
        with self._lock:
            count = len(self._inbox)
            self._inbox.clear()
            self._inbox_counts.clear()
            self._inbox_dropped = {}
            self._reset_inbox_window_locked()
        return count

    @property
    def inbox_size(self) -> int:
        with self._lock:
            return len(self._inbox)

    @property
    def inbox_ready(self) -> bool:
        """Whether queued notifications have waited out the batch window."""
        with self._lock:
            return self._inbox_batched

    @property
    def has_work(self) -> bool:
        """Whether a round can start now: a ready submission or batched notifications."""
        with self._lock:
            return self._inbox_batched or (
                self._submission is not None and self._submission.ready
            )


@dataclass
class ActivePrompt:
    id: str
    kind: str
    message: str
    choices: list[str]
    default: str
    done: threading.Event
    answer: str | None = None


# How often ``ask`` polls the terminal task for completion while a web
# client may answer concurrently.
PROMPT_POLL_INTERVAL = 0.05


def _run_prompt_coroutine(factory: Callable[[], Awaitable[None]]) -> None:
    """Run a prompt coroutine from synchronous code in any thread.

    ``asyncio.run`` cannot be called by a thread whose event loop is already
    running. In that uncommon case the synchronous API necessarily blocks its
    caller, so run the terminal adapter on a helper thread with its own loop.
    Async callers that need their loop to remain responsive should offload the
    synchronous :meth:`PromptBroker.ask` call to a worker thread.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(factory())
        return

    errors: list[BaseException] = []

    def run() -> None:
        try:
            asyncio.run(factory())
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run, name="ene-prompt-adapter", daemon=True)
    worker.start()
    worker.join()
    if errors:
        raise errors[0]


class PromptBroker:
    """Synchronize interactive prompts; the first valid answer wins."""

    def __init__(self, events: EventHub):
        self.events = events
        self._cond = threading.Condition()
        self._active: ActivePrompt | None = None
        self._terminal_asker: (
            Callable[[ActivePrompt], Awaitable[str | None]] | None
        ) = None
        self.cancellation: CancellationToken | None = None

    def set_terminal_adapter(
        self,
        asker: Callable[[ActivePrompt], Awaitable[str | None]],
    ) -> None:
        self._terminal_asker = asker

    @property
    def active(self) -> ActivePrompt | None:
        with self._cond:
            return self._active

    def ask(
        self,
        kind: str,
        message: str,
        choices: list[str] | None = None,
        default: str = "",
    ) -> str | None:
        prompt = ActivePrompt(
            id=uuid.uuid4().hex,
            kind=kind,
            message=message,
            choices=list(choices or []),
            default=default,
            done=threading.Event(),
        )
        with self._cond:
            # Queue behind any active prompt instead of failing the caller.
            while self._active is not None:
                self._cond.wait()
            if self.cancellation is not None and self.cancellation.cancelled:
                return None
            self._active = prompt
        self.events.publish(
            "prompt_open",
            id=prompt.id,
            kind=kind,
            message=message,
            choices=prompt.choices,
            default=default,
        )

        if self._terminal_asker is not None:
            async def ask_both() -> None:
                if prompt.done.is_set():
                    return
                terminal_task = asyncio.create_task(
                    self._terminal_asker(prompt)
                )
                while not prompt.done.is_set() and not terminal_task.done():
                    await asyncio.sleep(PROMPT_POLL_INTERVAL)
                if prompt.done.is_set():
                    terminal_task.cancel()
                    try:
                        await terminal_task
                    except asyncio.CancelledError:
                        pass
                    return
                try:
                    answer = terminal_task.result()
                except (EOFError, KeyboardInterrupt):
                    answer = None
                if answer is None:
                    self.cancel(prompt.id, source="terminal")
                else:
                    self.resolve(prompt.id, answer, source="terminal")

            try:
                _run_prompt_coroutine(ask_both)
            except BaseException:
                self.cancel(prompt.id, source="terminal")
                raise

        if not prompt.done.is_set():
            prompt.done.wait()
        return prompt.answer

    def resolve(self, prompt_id: str, answer: str, source: str = "web") -> bool:
        answer = sanitize_unicode(answer)
        with self._cond:
            prompt = self._active
            if prompt is None or prompt.id != prompt_id or prompt.done.is_set():
                return False
            if prompt.kind == "select" and answer not in prompt.choices:
                return False
            prompt.answer = answer
            prompt.done.set()
            self._active = None
            self._cond.notify_all()
        self.events.publish(
            "prompt_resolved", id=prompt_id, answer=answer, source=source
        )
        return True

    def cancel(self, prompt_id: str, source: str = "terminal") -> bool:
        with self._cond:
            prompt = self._active
            if prompt is None or prompt.id != prompt_id or prompt.done.is_set():
                return False
            prompt.answer = None
            prompt.done.set()
            self._active = None
            self._cond.notify_all()
        self.events.publish(
            "prompt_resolved", id=prompt_id, answer=None, source=source
        )
        return True


class CancellationToken:
    """Operation-scoped cancellation shared by terminal and web controls."""

    def __init__(self, events: EventHub, prompts: PromptBroker | None = None):
        self.events = events
        self.prompts = prompts
        if prompts is not None:
            prompts.cancellation = self
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._operation_id: str | None = None
        self.watch_keyboard = True

    def begin(self, label: str) -> str:
        with self._lock:
            self._event.clear()
            self._operation_id = uuid.uuid4().hex
            operation_id = self._operation_id
        self.events.publish("operation_start", id=operation_id, label=label)
        return operation_id

    @contextmanager
    def operation(self, label: str):
        """Publish and clean up an operation even when its body fails."""
        operation_id = self.begin(label)
        try:
            yield operation_id
        finally:
            self.finish(operation_id)

    def finish(self, operation_id: str) -> None:
        with self._lock:
            if self._operation_id != operation_id:
                return
            self._operation_id = None
            self._event.clear()
        self.events.publish("operation_end", id=operation_id)

    def cancel(self, operation_id: str | None = None) -> bool:
        with self._lock:
            if self._operation_id is None:
                return False
            if operation_id is not None and operation_id != self._operation_id:
                return False
            active = self._operation_id
            self._event.set()
        if self.prompts is not None:
            prompt = self.prompts.active
            if prompt is not None:
                self.prompts.cancel(prompt.id, source="operation")
        self.events.publish("operation_cancelled", id=active)
        return True

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def operation_id(self) -> str | None:
        with self._lock:
            return self._operation_id
