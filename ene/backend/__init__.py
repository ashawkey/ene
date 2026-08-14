import asyncio
import json
import os
import queue
import re
import threading
import time
from contextlib import nullcontext
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from prompt_toolkit.patch_stdout import patch_stdout

from ene.backend.batch import IsolatedTurnMixin
from ene.backend.commands import AgentCommandsMixin
from ene.personas import (
    DEFAULT_PERSONA,
    PersonaContext,
    PersonaInfo,
    discover_personas,
    get_persona,
)
from ene.backend.sessions import SessionMixin
from ene.backend.skill_commands import SkillCommandsMixin
from ene.terminal import PromptDriver, TerminalInput
from ene.ui import AgentConsole, ContextStatus
from ene.utils import get_ene_dir
from ene.skills import discover_skills
from ene.tools import (
    ToolExecutor,
    describe_tool_output,
    format_tool_result,
    format_tool_summary,
)
from ene.tools.process_manager import format_process_status
from ene.tools.results import (
    discard_tool_result_artifact,
    persist_tool_result_artifact,
    prune_tool_result_artifacts,
    read_tool_result_text,
)
from ene.providers import (
    CompletionRequest,
    ProviderSettings,
    ProviderUsage,
    create_provider,
)
from ene.context import (
    COMPACTION_INPUT_MAX_CHARS,
    COMPACTION_MIN_YIELD_RATIO,
    COMPACTION_SUMMARY_MAX_TOKENS,
    ContextManager,
    TokenEstimator,
    SOFT_TRIM_THRESHOLD,
    UNREPEATABLE_TOOLS,
    CompactionState,
    ToolResultEnvelope,
    compact_context,
    compact_tool_result_envelope,
    estimate_context_chars,
    needs_compaction,
    prune_context,
    tool_result_char_budget,
)
from ene.messages import ImagePart, Message, TextPart
from ene.utils.rewind import ChangeTracker
from ene.models import (
    REASONING_EFFORTS,
    ReasoningEffort,
    resolve_model_profile,
)
from ene.utils.interrupt import (
    run_interruptible,
    RequestInterrupted,
    TurnOutcome,
)
from ene.utils.io import (
    CancellationToken,
    EventHub,
    InputBroker,
    PromptBroker,
    UserSubmission,
)

# HTTP status codes that represent transient failures worth retrying even
# though they are 4xx. Everything else in the 4xx range is a permanent client
# error (bad key, bad request, unknown model, …) and must not be retried.
_RETRYABLE_STATUS_CODES = frozenset({408, 409, 425, 429})


# Providers report an oversized prompt as a plain 400, which is otherwise
# fatal. Matched on message text because no provider gives it a distinct code.
_CONTEXT_OVERFLOW_MARKERS = (
    "context_length_exceeded",
    "context length exceeded",
    "maximum context length",
    "prompt is too long",
    "reduce the length of the messages",
    "exceeds the context window",
)


def _is_context_overflow_error(exc: Exception) -> bool:
    """Whether *exc* says the request exceeded the model's context window."""
    text = str(exc).lower()
    return any(marker in text for marker in _CONTEXT_OVERFLOW_MARKERS)


def _is_fatal_api_error(exc: Exception) -> bool:
    """Whether *exc* is a permanent client error that retrying cannot fix.

    Provider errors may declare retryability explicitly. Otherwise HTTP 4xx
    responses other than a few transient ones (rate limit, timeout, conflict)
    are fatal; connection errors, timeouts, and 5xx responses keep retrying.

    Programming errors (TypeError, ValueError, …) are always fatal so the
    agent does not spin forever retrying a request that can never succeed.
    """
    retryable = getattr(exc, "retryable", None)
    if retryable is not None:
        return not retryable
    status = getattr(exc, "status_code", None)
    if status is None:
        # No HTTP status and no explicit retryability — the error came from
        # the local process, not the remote API.  Programming mistakes are
        # fatal; network / I/O errors remain retryable.
        if isinstance(exc, (TypeError, ValueError, KeyError, AttributeError, AssertionError)):
            return True
        return False
    return 400 <= status < 500 and status not in _RETRYABLE_STATUS_CODES


_AT_PATH_RE = re.compile(r"(?<!\S)@([\w./\\~+-]+)")


def _strip_at_marks(query: str) -> str:
    """Strip the ``@`` prefix from file-path references in *query*.

    Only matches ``@`` at a word boundary (preceded by whitespace or
    start-of-string) so email addresses like ``user@host.com`` are left
    untouched.
    """
    return _AT_PATH_RE.sub(r"\1", query)


def _is_local_query(query: str) -> bool:
    return query.lower() in ("exit", "quit") or query.startswith(("!", "/"))


class LLMAgent(
    AgentCommandsMixin, IsolatedTurnMixin, SkillCommandsMixin, SessionMixin
):
    INITIAL_BACKOFF = 1.0   # seconds
    MAX_BACKOFF = 64.0      # seconds
    MAX_AUTO_CONTINUES = 3

    def __init__(
        self, 
        model: str,
        api_key: str,
        base_url: str,
        provider_name: str = "openai",
        model_alias: str = "",
        verbose: bool = True,
        stream: bool = True,
        reasoning_effort: ReasoningEffort = "high",
        context_length: int | None = None,
        persona: str | None = None,
        exec_mode: bool = False,
        work_dir: str | None = None,
        console: AgentConsole | None = None,
        events: EventHub | None = None,
        input_broker: InputBroker | None = None,
        prompt_broker: PromptBroker | None = None,
        cancellation: CancellationToken | None = None,
        max_output_tokens: int | None = None,
        terminal_prompts: bool = True,
        session_name: str = "",
    ):

        self.model = model
        self.model_alias = model_alias
        self.profile = resolve_model_profile(model, model_alias)
        self.provider_name = provider_name
        self._provider_settings = ProviderSettings(
            api_key=api_key,
            base_url=base_url,
            reasoning_style=self.profile.reasoning,
        )
        self.provider = create_provider(provider_name, self._provider_settings)
        self._active_compaction_provider = None
        self.verbose = verbose
        self.stream = stream
        self.show_thinking = self.profile.reasoning is not None
        self.reasoning_effort = reasoning_effort
        self.context_length = context_length if context_length is not None else self.profile.context_length
        self.max_output_tokens = max_output_tokens if max_output_tokens is not None else self.profile.max_output_tokens
        self.token_estimator = TokenEstimator()

        self.events = events
        if input_broker is None:
            events = events or EventHub()
            self.events = events
            input_broker = InputBroker(events)
        self.input_broker = input_broker
        if prompt_broker is None:
            prompt_broker = PromptBroker(events)
        self.prompt_broker = prompt_broker
        if cancellation is None:
            cancellation = CancellationToken(events, prompt_broker)
        self.cancellation = cancellation
        if self.cancellation is not None and self.prompt_broker is not None:
            self.cancellation.prompts = self.prompt_broker
            self.prompt_broker.cancellation = self.cancellation
        self.console = console or AgentConsole(events=events)
        self.session_name = session_name
        self._session_changed = None
        self._session_name_changed = None

        self.console.prompt_broker = self.prompt_broker
        if terminal_prompts and self.prompt_broker is not None:
            async def terminal_ask_async(prompt):
                terminal_message = prompt.message.splitlines()[0]
                if prompt.kind == "select":
                    return await self.console.select_terminal_async(
                        terminal_message,
                        choices=prompt.choices,
                        default=prompt.default or None,
                    )
                return await self.console.ask_text_terminal_async(
                    terminal_message, default=prompt.default
                )

            self.prompt_broker.set_terminal_adapter(terminal_ask_async)

        self.exec_mode = exec_mode
        self.work_dir = str(Path(work_dir).absolute()) if work_dir else str(Path.cwd())

        skill_issues: dict = {}
        self.skills = discover_skills(self.work_dir, issues=skill_issues)
        persona_issues: dict = {}
        self.personas = discover_personas(self.work_dir, issues=persona_issues)
        self._report_skill_issues(skill_issues)
        self._report_persona_issues(persona_issues)

        self.persona: PersonaInfo = get_persona(
            persona or DEFAULT_PERSONA, personas=self.personas
        )
        self.system_prompt = self._build_system_prompt()

        self.changes: ChangeTracker | None = None
        self.tool_executor = ToolExecutor(
            console=self.console,
            work_dir=self.work_dir,
            skills=self.skills,
            cancellation=cancellation,
            isolated_turn=self.run_isolated_turn,
            model_alias=self.model_alias or None,
            reasoning_effort=self.reasoning_effort,
        )
        self._process_status_sink = None
        self.tool_executor.set_process_status_callback(self._process_status_changed)
        self.context = ContextManager(self.system_prompt)
        self._pending_images: list[dict[str, str]] = []
        # Whether a context-isolated turn owns the conversation right now; see
        # IsolatedTurnMixin.
        self._isolated_turn_active: bool = False

        self.round_id = 0
        self._session_id: str | None = None  # set by chat_loop
        self._session_store = None
        self._session_revision_id: str | None = None

        self._last_interrupted: bool = False   # set by get_response when a round is cancelled
        self._last_turn_outcome: TurnOutcome = TurnOutcome.COMPLETED
        self._last_error: str | None = None
        self._closed = False
        self._interrupt_reverts_prompt: bool = False
        self._failure_reverts_prompt: bool = False
        self._rewind_draft: str | None = None  # prompt restored after /rewind or cancellation

        self.token_totals = {
            "total": 0,
            "prompt": 0,
            "cached_prompt": 0,
            "completion": 0,
            "reasoning": 0,
        }
        self.tool_compaction_totals = {
            "calls": 0,
            "original_chars": 0,
            "retained_chars": 0,
        }
        self.compaction_totals = {
            "count": 0,
            "tokens_before": 0,
            "tokens_after": 0,
        }
        # Usage below which another compaction is not worth attempting; set
        # after every pass, so the next one waits for real growth rather than
        # firing again on the tool result that follows it.
        self._compaction_floor_tokens: int | None = None

        # self.console.system(f"System prompt: {self.system_prompt[:100]}...")


    @property
    def tools(self) -> list[dict[str, Any]]:
        """Tool schemas advertised to the API for the current turn.

        Computed live from the registry so it always reflects the persona, the
        currently-loaded skills, and image support — no manual rebuild needed.
        """
        return self.tool_executor.registry.advertised(
            persona_tools=self.persona.tools,
            supports_image=self.profile.supports_image_input,
        )

    def _messages_with_pending_images(self) -> list[Message]:
        messages = self.context.get()
        if not self._pending_images:
            return messages

        content: list[TextPart | ImagePart] = []
        for image in self._pending_images:
            content.extend((
                TextPart(f"Image returned by read_image: {image['file']}"),
                ImagePart({"url": image["url"]}),
            ))
        messages.append(Message.user(content))
        return messages

    def _build_system_prompt(self) -> str:
        """Build the system prompt via the active persona."""
        ctx = PersonaContext(
            exec_mode=self.exec_mode,
            work_dir=self.work_dir,
            skills=self.skills,
        )
        return self.persona.build(ctx)

    def _accumulate_usage(self, usage: ProviderUsage) -> None:
        """Add provider-neutral token counts to session totals."""
        self.token_totals["total"] += usage.total_tokens
        self.token_totals["prompt"] += usage.prompt_tokens
        self.token_totals["completion"] += usage.completion_tokens
        self.token_totals["cached_prompt"] += usage.cached_prompt_tokens
        self.token_totals["reasoning"] += usage.reasoning_tokens

    def _context_tokens(self) -> int:
        """Live prompt-token estimate for the next request.

        Anchored on the last count the API actually reported, so it includes
        the system prompt, tool schemas, and provider framing that character
        counting alone cannot see.
        """
        return self.token_estimator.prompt_tokens(self.context.total_chars)

    def _process_status_changed(self, running: int, finished: int) -> None:
        """Publish process counts plus live activity, if any."""
        status = format_process_status(
            running, finished, self.tool_executor.process_activity()
        )
        if self._process_status_sink is not None:
            self._process_status_sink(status)
        if self.events is not None:
            self.events.publish(
                "process_status", running=running, finished=finished, text=status
            )

    def _status_suffix(self) -> ContextStatus:
        """Context-window progress shown in the 'Working...' status bar."""
        return ContextStatus(
            tokens=self._context_tokens(),
            limit=self.context_length,
            input_tokens=self.token_totals["prompt"],
            output_tokens=self.token_totals["completion"],
            cached_tokens=self.token_totals["cached_prompt"],
        )

    def _interruptible_sleep(self, seconds: float):
        stop = threading.Event()
        run_interruptible(
            lambda: stop.wait(seconds), self.cancellation, on_cancel=stop.set
        )

    def _operation(self, label: str):
        if self.cancellation is None:
            return nullcontext()
        return self.cancellation.operation(label)

    def _create_change_tracker(
        self, session_id: str, work_dir: str, store, code_revision_id: str | None = None
    ):
        return ChangeTracker(session_id, work_dir, self.console, store, code_revision_id)

    def _ene_dir(self) -> Path:
        return get_ene_dir()

    def _session_timestamp(self) -> str:
        return time.strftime("%Y%m%d_%H%M%S")

    def _reserve_session_id(self) -> str:
        """Atomically reserve a unique timestamp-based session ID."""
        base_id = self._session_timestamp()
        session_id = base_id
        suffix = 2
        sessions_dir = self._sessions_dir()
        while True:
            if session_id == self._session_id:
                session_id = f"{base_id}_{suffix}"
                suffix += 1
                continue
            try:
                (sessions_dir / session_id).mkdir()
            except FileExistsError:
                session_id = f"{base_id}_{suffix}"
                suffix += 1
            else:
                return session_id

    def call_api(self):
        """Call the API using current context, with automatic retry on transient errors.

        Transient errors (connection failures, timeouts, 5xx, rate limits) are
        retried indefinitely with capped exponential backoff. Permanent client
        errors (auth, malformed request, unknown model, …) are re-raised as
        ``RuntimeError`` so ``get_response`` can end the turn gracefully.

        Atomic with respect to the conversation: the assistant message is
        appended as the last step, so every failure path leaves the history
        exactly as it was found — apart from eviction and compaction, which are
        window maintenance rather than turn state and are meant to persist.
        Responses cut off by the output limit or without a terminal finish
        reason are appended too; ``get_response`` then continues them.
        """

        # context management: evict old tool results, then compact if needed
        if self.context_length > 0:
            t_prune = time.monotonic()
            cpt = self.token_estimator.chars_per_token
            self.context.replace_messages(
                prune_context(
                    self.context.messages, self.context_length, cpt,
                    used_tokens=self._context_tokens(),
                    max_output_tokens=self.max_output_tokens,
                )
            )
            prune_elapsed = time.monotonic() - t_prune
            if self.verbose and prune_elapsed > 0.1:
                self.console.debug(f"Context eviction took {prune_elapsed:.2f}s")

            used_tokens = self._context_tokens()
            if needs_compaction(
                self.context.messages, self.context_length, cpt,
                used_tokens=used_tokens,
                max_output_tokens=self.max_output_tokens,
            ):
                floor = self._compaction_floor_tokens
                if floor is not None and used_tokens < floor:
                    if self.verbose:
                        self.console.debug(
                            f"Skipping compaction: waiting for growth since the "
                            f"last pass (~{used_tokens:,} tok, floor ~{floor:,})"
                        )
                else:
                    self._run_compaction("Context window pressure")

        if self.verbose:
            ctx_tokens = self._context_tokens()
            ctx_pct = ctx_tokens / self.context_length * 100 if self.context_length else 0
            self.console.debug(
                f"Calling API (round: {self.round_id}, "
                f"context: ~{ctx_tokens}tok / {self.context_length}tok [{ctx_pct:.0f}%])"
            )

        had_pending_images = bool(self._pending_images)

        def build_request() -> tuple[list[Message], CompletionRequest]:
            messages = self._messages_with_pending_images()
            return messages, CompletionRequest(
                model=self.model,
                messages=messages,
                tools=self.tools,
                stream=self.stream,
                max_output_tokens=self.max_output_tokens,
                reasoning_effort=self.reasoning_effort,
                # Providers use this to key their prompt cache. Isolated turns
                # share a prefix with each other but not with the conversation,
                # so they get their own key rather than repeatedly displacing
                # the cached prefix of the round they run inside.
                session_id=(
                    f"{self._session_id}-isolated"
                    if self._isolated_turn_active and self._session_id
                    else self._session_id
                ),
            )

        messages, request = build_request()

        # ---- retry loop with exponential backoff ----
        t_api = time.monotonic()
        retry_count = 0
        overflow_recovered = False
        wait_time = self.INITIAL_BACKOFF
        while True:
            try:
                result = (
                    self._stream_completion(request)
                    if self.stream
                    else self._blocking_completion(request)
                )
                break
            except RequestInterrupted:
                raise  # user cancelled — never retry, let get_response roll back
            except Exception as e:
                if _is_fatal_api_error(e):
                    # An oversized prompt is the one permanent error the client
                    # can fix by itself: the estimate was wrong. Compact once
                    # and retry. Checked inside the fatal branch so a retryable
                    # rate limit that happens to mention tokens cannot match.
                    if not overflow_recovered and _is_context_overflow_error(e):
                        overflow_recovered = True
                        if self._run_compaction("Context overflow reported by the API"):
                            messages, request = build_request()
                            continue
                    # Permanent client error — retrying cannot help. Surface it
                    # as RuntimeError so get_response ends the turn gracefully.
                    status = getattr(e, "status_code", "?")
                    raise RuntimeError(f"API request rejected (HTTP {status}): {e}") from e
                retry_count += 1
                self.console.system(
                    f"[Retry {retry_count}] {e} — retrying in {wait_time:.1f}s…"
                )
                with self.console.thinking(
                    label="Waiting to retry",
                    progress=True,
                    status_suffix=f"{wait_time:.1f}s",
                ):
                    self._interruptible_sleep(wait_time)
                wait_time = min(wait_time * 2, self.MAX_BACKOFF)
        api_elapsed = time.monotonic() - t_api

        if self.cancellation is not None and self.cancellation.cancelled:
            raise RequestInterrupted()

        message = result.message
        usage = result.usage or self._estimate_usage(message)
        finish_reason = result.finish_reason
        self._pending_images.clear()
        self._accumulate_usage(usage)
        # Only a real provider count is worth anchoring on; images are excluded
        # because their token cost is invisible to character counting.
        if result.usage is not None and not had_pending_images:
            self.token_estimator.observe(
                estimate_context_chars(messages), usage.prompt_tokens
            )

        if self.verbose:
            self.console.debug(
                f"API response in {api_elapsed:.1f}s — finish_reason: {finish_reason or 'N/A'}, "
                f"total_tokens: {usage.total_tokens} = "
                f"output: {usage.completion_tokens} "
                f"(reasoning: {usage.reasoning_tokens or 'N/A'}) "
                f"input: {usage.prompt_tokens} "
                f"(cached: {usage.cached_prompt_tokens or 'N/A'})"
            )
            tool_calls = message.tool_calls
            if tool_calls:
                self.console.debug(f"Requested tool calls: {len(tool_calls)}")

        self._last_finish_reason = finish_reason
        self.context.add(message)
        return message

    def _snapshot_before_compaction(self) -> None:
        """Save a revision so /rewind can undo a compaction that lost too much."""
        if not self._session_id or self._session_store is None:
            return
        if self._isolated_turn_active:
            # An isolated turn's messages are not the conversation, so
            # committing them would move the durable head onto a revision that
            # a later resume would restore instead of the real session. There is
            # nothing to rewind to either: the context is discarded regardless.
            return
        try:
            self.save_session(self._session_id, reason="pre-compaction")
        except Exception as e:
            # A missing rewind point is not worth failing the turn over.
            self.console.warn(f"Could not snapshot before compaction: {e}")

    def _run_compaction(self, reason: str) -> bool:
        """LLM-compact the history now. Returns whether the context shrank.

        Used both when usage crosses the threshold and when the API rejects a
        request as too long, so the caller can decide whether a retry is worth
        attempting.
        """
        if len(self.context.messages) <= 2:
            return False

        before_tokens = self._context_tokens()
        before_msgs = len(self.context.messages)
        self.console.system(f"{reason} — compacting via LLM summarization")
        self._snapshot_before_compaction()
        t_compact = time.monotonic()
        with self.console.thinking(
            label="Compacting",
            progress=True,
            status_suffix=f"{before_msgs} messages, ~{before_tokens:,} tokens",
        ):
            compacted, state = run_interruptible(
                lambda: compact_context(
                    self.context.messages, self._summarize,
                    console=self.console,
                    context_length=self.context_length,
                    chars_per_token=self.token_estimator.chars_per_token,
                    used_tokens=before_tokens,
                    max_output_tokens=self.max_output_tokens,
                    state=self.context.compaction_state,
                ),
                self.cancellation,
                on_cancel=lambda: self._cancel_compaction_provider(),
            )
        if self.cancellation is not None and self.cancellation.cancelled:
            raise RequestInterrupted()

        if compacted is self.context.messages:
            # Identity means no pass happened: everything old enough sits inside
            # the protected recent window, the split could not free enough to pay
            # for the round-trip, or the summarization failed (which warns on its
            # own). Either way the context is untouched.
            self.console.system("Nothing worth compacting — context unchanged.")
            if self.context_length > 0:
                self._compaction_floor_tokens = (
                    before_tokens + int(self.context_length * COMPACTION_MIN_YIELD_RATIO)
                )
            return False

        self.context.replace_messages(compacted)
        self.context.compaction_state = state
        after_tokens = self._context_tokens()
        after_msgs = len(self.context.messages)
        saved_pct = (1 - after_tokens / before_tokens) * 100 if before_tokens else 0
        self.console.system(
            f"Compaction complete ({time.monotonic() - t_compact:.1f}s): "
            f"{before_msgs} messages → {after_msgs} messages, "
            f"~{before_tokens:,} tokens → ~{after_tokens:,} tokens "
            f"(saved {saved_pct:.0f}%)"
        )

        self.compaction_totals["count"] += 1
        self.compaction_totals["tokens_before"] += before_tokens
        self.compaction_totals["tokens_after"] += after_tokens

        # Never compact again until the context has grown by at least the yield a
        # pass has to produce to be worth its round-trip. Set unconditionally: a
        # pass that clears the bar by a hair used to reset the floor to None,
        # leaving the marginal pass right behind it unguarded.
        if self.context_length > 0:
            self._compaction_floor_tokens = (
                after_tokens + int(self.context_length * COMPACTION_MIN_YIELD_RATIO)
            )
        return before_tokens > after_tokens

    def _summary_provider(self):
        """Return the configured compaction provider, model, and ownership flag."""
        from ene.config import conf

        alias = conf.get("summary_model")
        if alias is None:
            return self.provider, self.model, False
        if not isinstance(alias, str) or not alias.strip():
            raise ValueError("'summary_model' must be a non-empty model alias")
        alias = alias.strip()
        model_conf = conf.get("openai", {}).get(alias)
        if not isinstance(model_conf, dict):
            raise ValueError(
                f"Summary model '{alias}' not found under 'openai' in the configuration"
            )
        model = model_conf.get("model", alias)
        profile = resolve_model_profile(model, alias)
        provider = create_provider(
            model_conf.get("provider", "openai"),
            ProviderSettings(
                api_key=model_conf.get("api_key", ""),
                base_url=model_conf.get("base_url", ""),
                reasoning_style=profile.reasoning,
            ),
        )
        return provider, model, True

    def _cancel_compaction_provider(self) -> None:
        """Cancel the provider currently handling the summary request, if any."""
        provider = self._active_compaction_provider
        if provider is not None:
            provider.cancel()

    def _summarize(self, prompt: str) -> str:
        """Run a provider-neutral non-streaming compaction request.

        Deliberately not run at the session's reasoning effort: rewriting a
        conversation into a fixed section structure is a transcription task, and
        at the default "high" it costs far more latency than the summary is
        worth. The output cap bounds what the pass writes back into the window.
        """
        provider = None
        owned = False
        try:
            provider, model, owned = self._summary_provider()
            self._active_compaction_provider = provider
            if self.cancellation is not None and self.cancellation.cancelled:
                raise RequestInterrupted()
            result = provider.complete(CompletionRequest(
                model=model,
                messages=[Message.user(prompt)],
                stream=False,
                timeout=60,
                max_output_tokens=COMPACTION_SUMMARY_MAX_TOKENS,
                reasoning_effort="low",
            ))
        except Exception:
            # Escape calls provider.cancel(), which tears down the in-flight
            # request and surfaces here as a transport error on the summarization
            # worker thread. Translate it, so compaction unwinds quietly instead
            # of reporting a failure the user caused on purpose.
            if self.cancellation is not None and self.cancellation.cancelled:
                raise RequestInterrupted() from None
            raise
        finally:
            try:
                if owned and provider is not None:
                    provider.close()
            finally:
                self._active_compaction_provider = None
        if result.usage is not None:
            self._accumulate_usage(result.usage)
        summary = result.message.text
        if not summary:
            raise RuntimeError("Compaction provider returned no summary text")
        return summary

    def _blocking_completion(self, request: CompletionRequest):
        """Execute a non-streaming provider request with cancellation."""
        with self.console.thinking(status_suffix=self._status_suffix()):
            return run_interruptible(
                lambda: self.provider.complete(request),
                self.cancellation,
                on_cancel=self.provider.cancel,
            )

    def _stream_completion(self, request: CompletionRequest):
        """Stream a provider request to the terminal and web sinks."""
        with self.console.stream_response(show_thinking=self.show_thinking) as sink:
            def consume():
                stream = None
                try:
                    stream = self.provider.open_stream(request)
                    return stream.consume(
                        on_content=sink.on_content,
                        on_thinking=sink.on_thinking,
                        should_stop=lambda: (
                            self.cancellation is not None
                            and self.cancellation.cancelled
                        ),
                    )
                finally:
                    if stream is not None:
                        stream.close()

            with self.console.thinking(status_suffix=self._status_suffix()):
                return run_interruptible(
                    consume, self.cancellation, on_cancel=self.provider.cancel
                )

    def _estimate_usage(self, message) -> ProviderUsage:
        """Build rough provider-neutral usage when an API omits it."""
        prompt_chars = estimate_context_chars(self.context.get())
        prompt_tokens = self.token_estimator.chars_to_tokens(prompt_chars)
        completion_chars = len(message.text)
        for tool_call in message.tool_calls or []:
            completion_chars += len(tool_call.arguments)
        completion_tokens = self.token_estimator.chars_to_tokens(completion_chars)
        return ProviderUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )



    def execute_tool_calls(self, tool_calls: list) -> TurnOutcome:
        """Execute one assistant tool-call batch via the built-in ToolExecutor.

        Returns ``USER_INTERRUPTED`` if the round was cancelled (ESC / Ctrl+C /
        web cancel) partway through, in which case any remaining tool calls are
        answered with a synthetic "skipped" result so the assistant/tool message
        pairing stays valid, and the caller should return to the prompt.
        """

        t_all = time.monotonic()
        interrupted = False
        for i, tool_call in enumerate(tool_calls):
            function_name = tool_call.name

            # A user cancel aborts the whole round; fill remaining calls with
            # skipped results to keep assistant/tool pairing valid.
            if self.cancellation is not None and self.cancellation.cancelled:
                interrupted = True
            if interrupted:
                self.context.add(Message.tool(
                    tool_call.id,
                    "Tool call skipped: the user interrupted the turn.",
                ))
                continue

            parse_error = None
            try:
                function_args = json.loads(tool_call.arguments)
            except json.JSONDecodeError as exc:
                function_args = {}
                parse_error = str(exc)

            if parse_error is not None:
                self.console.error(f"Failed to parse tool args: {parse_error}")
            
            if self.verbose:
                self.console.debug(f"Tool call {i+1}/{len(tool_calls)}: {function_name}({function_args})")

            if self.cancellation is not None and self.cancellation.cancelled:
                interrupted = True
                self.context.add(Message.tool(
                    tool_call.id,
                    "Tool call skipped: the user interrupted the turn.",
                ))
                continue

            if parse_error is not None:
                result = {"error": f"Invalid tool arguments: {parse_error}", "success": False}
            elif self.cancellation is not None and self.cancellation.cancelled:
                result = {
                    "error": "Tool call skipped: the user interrupted the turn.",
                    "success": False,
                    "interrupted": True,
                }
            else:
                with self.console.thinking(label="Executing", status_suffix=function_name):
                    result = self.tool_executor.execute(function_name, function_args)
            image_url = result.pop("image_url", None)
            if image_url and result.get("success"):
                self._pending_images.append({
                    "file": function_args["file"],
                    "url": image_url,
                })
            result_text = format_tool_result(result)

            success = result.get("success", False)
            registry = getattr(self.tool_executor, "registry", None)
            spec = registry.get(function_name) if registry is not None else None
            output_description = describe_tool_output(
                function_name,
                result,
                spec.describe_output if spec is not None else None,
            )
            if result.get("streamed"):
                self.console.tool_result(output_description, success=success)
            elif function_name in ("edit_file", "write_file") and "diff" in result:
                self.console.diff_edit(**result["diff"], success=success)
            elif function_name == "multi_edit":
                if result.get("diffs"):
                    for d in result["diffs"]:
                        self.console.diff_edit(**d, success=success)
                else:
                    self.console.tool_result(output_description, success=success)
            else:
                self.console.tool_result(output_description, success=success)

            envelope = ToolResultEnvelope(function_name, function_args, result, result_text)
            budget = tool_result_char_budget(
                self.context_length,
                self.token_estimator.chars_per_token,
                function_name,
            )
            if envelope.original_chars > budget:
                try:
                    compaction_text = read_tool_result_text(
                        result, result_text, max_chars=COMPACTION_INPUT_MAX_CHARS
                    )
                except OSError as e:
                    compaction_text = result_text
                    self.console.warn(f"Could not read captured tool output: {e}")
                if function_name == "exec_command" and compaction_text != result_text:
                    status = result_text.rsplit("\n", 1)[-1]
                    separator = "" if compaction_text.endswith("\n") else "\n"
                    compaction_text = f"{compaction_text}{separator}{status}"

                try:
                    artifact_path = persist_tool_result_artifact(
                        function_name,
                        compaction_text,
                        result,
                        tool_call.id,
                        self.work_dir,
                        self._session_id,
                        self.round_id,
                    )
                except (OSError, ValueError) as e:
                    artifact_path = None
                    self.console.warn(f"Could not save compacted tool output: {e}")

                compacted = compact_tool_result_envelope(
                    ToolResultEnvelope(function_name, function_args, result, compaction_text),
                    self.context_length,
                    self.token_estimator.chars_per_token,
                    artifact_path=artifact_path,
                )
                result_text = compacted.text
                self.tool_compaction_totals["calls"] += 1
                self.tool_compaction_totals["original_chars"] += compacted.original_chars
                self.tool_compaction_totals["retained_chars"] += compacted.retained_chars
                notice = f"; full captured output: {artifact_path}" if artifact_path else ""
                self.console.system(
                    f"Compacted {function_name} result "
                    f"({compacted.original_chars:,}→{compacted.retained_chars:,} chars){notice}"
                )
            elif (
                function_name in UNREPEATABLE_TOOLS
                and envelope.original_chars > SOFT_TRIM_THRESHOLD
            ):
                # Small enough to enter history whole, big enough for layer 2 to
                # trim later — and this output cannot be produced a second time.
                # The pointer goes in the message text rather than a side table
                # so it survives eviction and session save/resume alike.
                try:
                    artifact_path = persist_tool_result_artifact(
                        function_name,
                        result_text,
                        result,
                        tool_call.id,
                        self.work_dir,
                        self._session_id,
                        self.round_id,
                    )
                except (OSError, ValueError) as e:
                    self.console.warn(f"Could not save tool output: {e}")
                else:
                    result_text += f"\n[Captured output: {artifact_path}.]"
            else:
                cleanup_error = discard_tool_result_artifact(result)
                if cleanup_error:
                    self.console.warn(f"Could not remove temporary tool output: {cleanup_error}")

            tool_message = Message.tool(
                tool_call.id,
                result_text,
                display_content=(
                    output_description
                    if output_description != format_tool_summary(result_text)
                    else None
                ),
            )
            self.context.add(tool_message)

            # Detect a user interrupt: either the tool self-reported it
            # (e.g. exec_command killed by ESC) or the shared cancellation
            # token was tripped (web cancel). Abort the remaining calls.
            if result.get("interrupted") or (
                self.cancellation is not None and self.cancellation.cancelled
            ):
                interrupted = True

        total_elapsed = time.monotonic() - t_all
        if self.verbose and len(tool_calls) > 1:
            self.console.debug(f"All {len(tool_calls)} tool calls completed in {total_elapsed:.1f}s")

        return TurnOutcome.USER_INTERRUPTED if interrupted else TurnOutcome.COMPLETED

    def _resolve_unexecuted_tool_calls(self, message: dict[str, Any]) -> None:
        """Keep history valid when a turn ends on tool calls that never ran.

        Providers reject an assistant message whose tool calls have no matching
        results, so leaving the pair unresolved would fail every later request
        in the session rather than just this round. Each call is answered with a
        synthetic result, exactly as an interrupted round does. A call that
        arrived without an id cannot be answered at all — the truncation cut it
        that early — so the whole message is withdrawn instead.
        """
        tool_calls = message.tool_calls or []
        if all(tool_call.id for tool_call in tool_calls):
            for tool_call in tool_calls:
                self.context.add(Message.tool(
                    tool_call.id,
                    (
                        "Tool call skipped: the response was cut off before the "
                        "call was complete, so it was never executed."
                    ),
                ))
        else:
            self.context.drop_last(message)

    def _inject_pending_steer(self) -> bool:
        """Add pending conversational input before the next agentic iteration."""
        if self.input_broker is None:
            return False
        if self._isolated_turn_active:
            # An isolated turn's context is discarded when it ends, so steering
            # into it would consume the user's message and then throw it away.
            # Leaving it pending lets the enclosing round pick it up instead.
            return False

        submission = self.input_broker.submission
        if submission is None or not submission.steer:
            return False
        query = _strip_at_marks(submission.text.strip())
        if not query or _is_local_query(query):
            return False
        try:
            submission = self.input_broker.get_nowait(submission.id)
        except queue.Empty:
            return False

        self.console.user_input(
            query,
            source=submission.source,
            submission_id=submission.id,
            steer=True,
        )
        self.context.add(Message.user(query))
        return True

    def get_response(self):
        """Process the context and update the current response.

        Fatal API errors (auth, quota, bad request, …) are caught, displayed
        to the user, and cause the turn to end gracefully rather than crash.
        """

        iteration = 0
        t_turn_start = time.monotonic()
        self._last_interrupted = False
        self._last_turn_outcome = TurnOutcome.COMPLETED
        self._last_error = None
        self._interrupt_reverts_prompt = False
        self._failure_reverts_prompt = False
        turn_has_response = False
        auto_continues = 0

        while True:
            iteration += 1
            events = getattr(self, "events", None)
            if events is not None:
                events.publish(
                    "iteration_start", iteration=iteration, round_id=self.round_id
                )
            t_iter = time.monotonic()
            if self.verbose and iteration > 1:
                self.console.debug(f"--- Agentic loop iteration {iteration} ---")

            # call_api appends the assistant message only on success, so a failed
            # attempt leaves no partial assistant turn behind. Context maintenance
            # performed before the request remains in place for non-interactive
            # callers; the interactive round wrapper restores its pre-submit
            # snapshot when returning the failed prompt to the editor.
            try:
                # A test double or alternate caller that replaces call_api is
                # a normal completed response unless it explicitly overrides
                # this value.
                self._last_finish_reason = "stop"
                message = self.call_api()
            except RequestInterrupted:
                self._pending_images.clear()
                self._last_turn_outcome = TurnOutcome.USER_INTERRUPTED
                self.console.system("Request cancelled.")
                self._last_interrupted = True
                self._interrupt_reverts_prompt = not turn_has_response
                return None
            except RuntimeError as e:
                self._pending_images.clear()
                self._last_turn_outcome = TurnOutcome.FAILED
                self._last_error = str(e)
                self._failure_reverts_prompt = not turn_has_response
                self.console.error(f"API call failed: {e}")
                return None

            # call_api appends the completed assistant message. From this point
            # cancellation must preserve the turn (including tool results from
            # completed iterations) rather than withdrawing the user's prompt.
            turn_has_response = True
            content = message.text
            if content:
                # The stream sink renders buffered Markdown and emits the final
                # event on close, so avoid printing it twice here.
                if not self.stream:
                    self.console.response(content)

            finish_reason = self._last_finish_reason
            # An assistant turn with neither text nor tool calls is unfinished
            # whatever the provider reported: the model spent the round on
            # reasoning alone (or the visible part was dropped) and the task is
            # left mid-flight, so it needs the same continuation as a truncated
            # response rather than ending the turn silently.
            empty_response = not message.text.strip() and not message.tool_calls
            if finish_reason in (None, "length") or empty_response:
                if message.tool_calls:
                    self.console.warn(
                        "Response was truncated during a tool call; cannot "
                        "automatically continue safely."
                    )
                    self._resolve_unexecuted_tool_calls(message)
                    return content or None
                if empty_response and not message.provider_state:
                    # Nothing in this message reaches the next request: no text,
                    # no tool call, no provider state to replay. Left in history
                    # it is re-sent every round for the rest of the session (and
                    # some providers reject a contentless assistant turn), so the
                    # continuation restarts from the exact pre-call context.
                    self.context.drop_last(message)
                if auto_continues >= self.MAX_AUTO_CONTINUES:
                    self.console.warn(
                        "Response is still unfinished after "
                        f"{auto_continues} automatic continuations; stopping."
                    )
                    return content or None
                auto_continues += 1
                if finish_reason == "length":
                    reason = "the output-token limit was reached"
                elif finish_reason is None:
                    reason = "the response ended without a terminal finish reason"
                else:
                    reason = "the response carried no text or tool call"
                self.console.warn(
                    f"Unfinished response ({reason}); automatically continuing "
                    f"({auto_continues}/{self.MAX_AUTO_CONTINUES})."
                )
                continue

            if not message.tool_calls:
                if self.verbose:
                    turn_elapsed = time.monotonic() - t_turn_start
                    self.console.debug(f"Turn complete: {iteration} iteration(s) in {turn_elapsed:.1f}s")
                return content or None

            outcome = self.execute_tool_calls(message.tool_calls)
            # A skill loaded this round may contribute tools; `self.tools` is a
            # live registry view, so the next loop iteration advertises them
            # automatically with no manual rebuild.

            if outcome is True or outcome == TurnOutcome.USER_INTERRUPTED:
                # The user cancelled a tool mid-round. Stop the agentic loop
                # and return to the prompt instead of feeding the (partial)
                # tool results back to the model for another iteration.
                self._pending_images.clear()
                self.console.system("Turn interrupted.")
                self._last_interrupted = True
                self._last_turn_outcome = outcome
                return None

            self._inject_pending_steer()

            if self.verbose:
                iter_elapsed = time.monotonic() - t_iter
                self.console.debug(f"Iteration {iteration} total: {iter_elapsed:.1f}s")

    # ----- bash command (!) ------------------------------------------------

    def _run_bash_command(self, command: str) -> None:
        """Execute a shell command starting with '!' directly, without sending to the model.

        Output is streamed in real-time and the command can be interrupted via Ctrl+C.
        """
        self.console.tool(f"! {command}")
        arguments = {"command": command}
        with AgentCommandsMixin._round_timer(self):
            with self._operation("shell command"):
                with self.console.thinking(label="Executing", status_suffix="exec_command"):
                    result = self.tool_executor.execute("exec_command", arguments)
        success = result.get("success", False)
        registry = getattr(self.tool_executor, "registry", None)
        spec = registry.get("exec_command") if registry is not None else None
        self.console.tool_result(
            describe_tool_output(
                "exec_command",
                result,
                spec.describe_output if spec is not None else None,
            ),
            success=success,
        )
        cleanup_error = discard_tool_result_artifact(result)
        if cleanup_error:
            self.console.warn(f"Could not remove temporary tool output: {cleanup_error}")

    def _restart_session(self):
        """Save the current session and start a fresh one for a persona change."""
        if self._session_id and self.context.messages:
            try:
                self.save_session(self._session_id)
                self.console.system(f"Session '{self._session_id}' saved.")
            except Exception as e:
                self.console.warn(f"Could not save session before clear: {e}")

        # Reserve the directory so concurrent agents cannot choose the same ID.
        session_id = self._reserve_session_id()
        if self._session_changed is not None:
            self._session_changed(session_id, self.session_name)
        self._session_id = session_id
        self._session_store = self._session_store_for(session_id)
        self._session_revision_id = None
        self.context.replace_messages([])
        self.context.compaction_state = CompactionState()
        self._pending_images.clear()
        self._isolated_turn_active = False
        self.round_id = 0
        self.token_totals = {key: 0 for key in self.token_totals}
        self.tool_compaction_totals = {key: 0 for key in self.tool_compaction_totals}
        self.compaction_totals = {key: 0 for key in self.compaction_totals}
        self._compaction_floor_tokens = None
        self.tool_executor.shutdown_processes(clear=True)
        self.tool_executor.reset_skill_tools()
        self.tool_executor._loaded_skills.clear()
        self.tool_executor._skill_loads.clear()
        self._last_interrupted = False
        self._last_turn_outcome = TurnOutcome.COMPLETED
        # Create and persist the branch root before the first round.
        self._install_change_tracker()
        self.save_session(self._session_id, reason="initial")
        self.console.system(f"Started new session '{self._session_id}'.")

    # ----- main loops -------------------------------------------------------

    def _run_terminal_loop(self, terminal: TerminalInput) -> None:
        """Keep the editor active while agent rounds run on a worker thread."""
        assert self.input_broker is not None
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ene-agent")
        active: Future | None = None
        exit_requested = False
        should_exit = False
        prompts: PromptDriver | None = None

        def cancel() -> None:
            if self.cancellation is not None:
                self.cancellation.cancel()

        def pending_text() -> str | None:
            item = self.input_broker.submission
            return item.text if item is not None else None

        def edit_pending() -> str | None:
            item = self.input_broker.withdraw()
            if item is not None:
                terminal.app.invalidate()
            return item.text if item is not None else None

        terminal.set_runtime_state(
            cancel=cancel,
            pending_text=pending_text,
            edit_pending=edit_pending,
            can_submit_while_pending=self.is_instant_command,
        )
        if self.cancellation is not None:
            self.cancellation.watch_keyboard = False
        self.console.interactive_input = True
        self.console.status_sink = terminal.set_status

        set_process_status = getattr(terminal, "set_process_status", None)
        self._process_status_sink = set_process_status
        if set_process_status is not None:
            self._process_status_changed(*self.tool_executor.process_counts())

        async def loop() -> None:
            nonlocal active, exit_requested, should_exit, prompts
            ui_loop = asyncio.get_running_loop()
            input_ready = asyncio.Event()
            wait_indicator = None

            def stop_wait_indicator() -> None:
                nonlocal wait_indicator
                if wait_indicator is not None:
                    wait_indicator.__exit__(None, None, None)
                    wait_indicator = None

            def wake_input() -> None:
                def refresh() -> None:
                    input_ready.set()
                    terminal.app.invalidate()

                ui_loop.call_soon_threadsafe(refresh)

            self.input_broker.add_listener(wake_input)
            prompts = PromptDriver(terminal)
            prompts.start()

            def reset_timeline() -> None:
                future = asyncio.run_coroutine_threadsafe(
                    prompts.reset_timeline(), ui_loop
                )
                future.result()

            self.console.timeline_reset_sink = reset_timeline

            async def show_prompt(prompt):
                async with prompts.paused():
                    terminal_message = prompt.message.splitlines()[0]
                    if prompt.kind == "select":
                        return await self.console.select_terminal_async(
                            terminal_message,
                            choices=prompt.choices,
                            default=prompt.default or None,
                        )
                    return await self.console.ask_text_terminal_async(
                        terminal_message, default=prompt.default
                    )

            async def terminal_ask(prompt):
                future = asyncio.run_coroutine_threadsafe(
                    show_prompt(prompt), ui_loop
                )
                return await asyncio.wrap_future(future)

            if self.prompt_broker is not None:
                self.prompt_broker.set_terminal_adapter(terminal_ask)

            try:
                while True:
                    item = self.input_broker.submission
                    should_show_wait = (
                        active is None
                        and item is not None
                        and item.ready_at is not None
                        and not item.ready
                    )
                    if should_show_wait and wait_indicator is None:
                        wait_indicator = self.console.thinking(
                            label="Waiting",
                            countdown=max(0, item.ready_at - time.monotonic()),
                        )
                        wait_indicator.__enter__()
                    elif not should_show_wait:
                        stop_wait_indicator()

                    input_task = asyncio.create_task(input_ready.wait())
                    prompt_task = prompts.task
                    wait = {input_task}
                    if prompt_task is not None:
                        wait.add(prompt_task)
                    if active is not None:
                        wait.add(asyncio.wrap_future(active))
                    done, _ = await asyncio.wait(
                        wait, return_when=asyncio.FIRST_COMPLETED
                    )
                    if input_task in done:
                        input_ready.clear()
                    else:
                        input_task.cancel()

                    if active is not None and active.done():
                        should_exit = active.result()
                        active = None
                        terminal.set_busy(False)
                        if exit_requested or should_exit:
                            return
                        draft = getattr(self, "_rewind_draft", None)
                        if draft is not None:
                            self._rewind_draft = None
                            await prompts.restart(draft)
                        if self.input_broker.ready:
                            stop_wait_indicator()
                            active = executor.submit(self._run_round)
                            terminal.set_busy(True)

                    if self.input_broker.pending:
                        if active is None and self.input_broker.ready:
                            stop_wait_indicator()
                            active = executor.submit(self._run_round)
                            terminal.set_busy(True)
                        else:
                            # The round owns the conversation, not this loop, so
                            # a command that stays clear of it answers now.
                            self._run_instant_command()

                    # Only act on the prompt this iteration waited on: a pause
                    # that started meanwhile owns the editor and will restart it.
                    if prompt_task is not None and prompt_task in done and prompts.task is prompt_task:
                        try:
                            text = prompt_task.result()
                        except asyncio.CancelledError:
                            if prompts.suspended:
                                await prompts.wait_resumed()
                            else:
                                # Cancelled with no pause owning it; reopen the
                                # editor so the loop cannot spin on a done task.
                                await prompts.restart()
                            continue
                        except (EOFError, KeyboardInterrupt):
                            if active is None:
                                return
                            exit_requested = True
                            cancel()
                            await prompts.restart()
                            continue

                        text = text.strip()
                        if not text:
                            await prompts.restart()
                            continue
                        if (
                            self.input_broker.pending
                            and text.startswith("/")
                            and self.is_instant_command(text)
                        ):
                            self.console.user_input(text, source="terminal")
                            self._run_command(text)
                        else:
                            try:
                                self.input_broker.submit(text, source="terminal")
                            except ValueError as exc:
                                await prompts.restart(text)
                                self.console.warn(str(exc))
                                continue
                        await prompts.restart()
                        terminal.app.invalidate()
                        if active is None and self.input_broker.ready:
                            stop_wait_indicator()
                            active = executor.submit(self._run_round)
                            terminal.set_busy(True)
                    elif prompt_task is not None and prompt_task in done:
                        await prompts.wait_resumed()
            finally:
                stop_wait_indicator()
                self.input_broker.remove_listener(wake_input)
                await prompts.stop()
                self.console.timeline_reset_sink = None
                if active is not None:
                    await asyncio.wrap_future(active)

        try:
            with patch_stdout(raw=True):
                asyncio.run(loop())
        finally:
            executor.shutdown(wait=True)
            if self.cancellation is not None:
                self.cancellation.watch_keyboard = True
            self._process_status_sink = None
            if set_process_status is not None:
                set_process_status("")
            self.console.interactive_input = False
            self.console.status_sink = None
            self.console.timeline_reset_sink = None

    def _run_round(self) -> bool:
        """Run one queued submission, absorbing an unexpected failure.

        Rounds execute on a worker thread whose future the UI loop reads. An
        exception escaping here would tear the whole chat session down over a
        single bad turn, so it is reported and the session stays at the prompt —
        matching how get_response already handles API failures.
        """
        try:
            return self._process_next_submission()
        except Exception as e:
            self.console.warn(f"Round failed: {e}", exc_info=self.verbose)
            return False

    def _process_next_submission(self) -> bool:
        assert self.input_broker is not None
        try:
            submission = self.input_broker.get_nowait()
        except queue.Empty:
            return False
        return self._process_query(submission)

    def _run_command(self, query: str) -> bool:
        """Dispatch a built-in /command. Returns True when chat should stop."""
        cmd_word = query.split()[0][1:].lower()
        if cmd_word in self.COMMANDS:
            return self._handle_command(query)
        self.console.warn(
            f"Unknown command: /{cmd_word}. Type /help for available commands."
        )
        return False

    def _skill_invocation_prompt(self, name: str, arguments: str) -> str:
        """Build the model-facing request for an explicit /skill invocation."""
        if arguments:
            return arguments
        return (
            f"The user explicitly invoked the '{name}' skill without additional task "
            "context. If SKILL.md clearly defines a `Default invocation` workflow, "
            "perform it. Otherwise, do not infer or start a task; briefly acknowledge "
            "the loaded skill and ask what the user wants to do with it."
        )

    def _prepare_skill_invocation(self, query: str) -> str | None:
        """Load an explicitly invoked skill and return its user instruction."""
        parts = query.split(maxsplit=1)
        name = parts[0][1:].lower()
        arguments = parts[1] if len(parts) > 1 else ""
        if self._record_skill_load(name) is None:
            return None
        self.console.system(
            f"Loaded skill '{name}' into context. It will guide the next response."
        )
        return self._skill_invocation_prompt(name, arguments.strip())

    def _run_instant_command(self) -> bool:
        """Run the pending submission now when it is safe to run mid-round.

        Called on the UI thread while a round runs on the worker thread, from
        either terminal or web input. Commands the round cannot conflict with
        (see :attr:`INSTANT_COMMANDS`) answer straight away; everything else
        stays pending and runs once the round ends. Returns whether one ran.
        """
        assert self.input_broker is not None
        submission = self.input_broker.submission
        if submission is None:
            return False
        query = submission.text.strip()
        if (
            not submission.ready
            or not query.startswith("/")
            or not self.is_instant_command(query)
        ):
            return False
        try:
            submission = self.input_broker.get_nowait(submission.id)
        except queue.Empty:
            return False  # withdrawn for editing while we were reading it
        self.console.user_input(
            query, source=submission.source, submission_id=submission.id
        )
        self._run_command(query)
        return True

    def _process_query(self, submission: UserSubmission) -> bool:
        """Process one user submission. Return True when chat should exit."""
        query = _strip_at_marks(submission.text.strip())
        if not query:
            return False
        self.console.rule()
        self.console.user_input(
            query,
            source=submission.source,
            submission_id=submission.id,
            with_rule=False,
        )
        if query.lower() in ("exit", "quit"):
            return True
        if query.startswith("/"):
            cmd_word = query.split()[0][1:].lower()
            if submission.source == "web" and cmd_word in {"resume", "rewind"}:
                self.console.warn(
                    f"/{cmd_word} is only available from an attached terminal."
                )
                return False
        if query.startswith("!"):
            bash_cmd = query[1:].strip()
            if bash_cmd:
                self._run_bash_command(bash_cmd)
            else:
                self.console.warn("Usage: !<shell command>")
            return False
        messages_before_round = list(self.context.messages)
        compaction_state_before_round = self.context.compaction_state
        round_before_round = self.round_id
        revision_before_round = self._session_revision_id
        compaction_floor_before_round = self._compaction_floor_tokens
        skill_state_before_round = None
        if query.startswith("/"):
            if cmd_word in self.COMMANDS:
                return self._run_command(query)
            if cmd_word not in self.skills:
                return self._run_command(query)
            skill_state_before_round = self.tool_executor.skill_state()
            model_query = self._prepare_skill_invocation(query)
            if model_query is None:
                return False
        else:
            model_query = query

        self.context.add(Message.user(
            model_query,
            display_content=query if model_query != query else None,
        ))
        self.round_id += 1
        self.console.rule()
        with AgentCommandsMixin._round_timer(self):
            with self._operation("agent response"):
                self.get_response()

        restore_prompt = (
            self._last_interrupted and self._interrupt_reverts_prompt
        ) or (
            getattr(self, "_last_turn_outcome", None) == TurnOutcome.FAILED
            and getattr(self, "_failure_reverts_prompt", False)
        )
        if restore_prompt:
            # No assistant message was completed, so withdraw the untouched
            # prompt and restore the exact pre-submit state. External side
            # effects cannot exist yet because no tool call was received.
            self.context.replace_messages(messages_before_round)
            self.context.compaction_state = compaction_state_before_round
            self._session_revision_id = revision_before_round
            self._compaction_floor_tokens = compaction_floor_before_round
            if skill_state_before_round is not None:
                self.tool_executor.restore_skill_state(skill_state_before_round)
            self.round_id = round_before_round
            self.console.reset_timeline()
            self._replay_context()
            self._set_rewind_draft(submission.text)
            if not self._last_interrupted and self._last_error:
                # The timeline reset removed the first rendering of this error.
                self.console.error(f"API call failed: {self._last_error}")
            action = "cancelled" if self._last_interrupted else "failed"
            self.console.system(
                f"Turn {action}. Message restored to the editor."
            )
        # If interruption happened after at least one assistant/tool step,
        # keep that useful history as-is; get_response already reported it.

        try:
            self.save_session(self._session_id, reason="round")
        except Exception as e:
            self.console.warn(f"Could not save session round: {e}")
        return False

    def _initialize_chat_session(self, resumed_session_id: str | None = None) -> None:
        """Initialize persistence, change tracking, and artifact retention."""
        self._session_id = resumed_session_id or self._reserve_session_id()
        if self._session_store is None or self._session_store.session_id != self._session_id:
            self._session_store = self._session_store_for(self._session_id)
            self._session_revision_id = self._session_store.head_id
        if self.changes is None or self.changes.session_id != self._session_id:
            self._install_change_tracker()
        if not self._session_store.exists:
            self.save_session(self._session_id, reason="initial")

        try:
            pruned = prune_tool_result_artifacts(self.work_dir, self._session_id)
        except OSError as e:
            # Stale captures cost disk, not correctness — never fail startup.
            self.console.warn(f"Could not prune old tool-result captures: {e}")
        else:
            if pruned and self.verbose:
                self.console.debug(
                    f"Pruned tool-result captures from {pruned} old session(s)"
                )

    def run_headless_loop(self, stop_event: threading.Event) -> None:
        """Consume broker input while a detached worker owns the agent."""
        assert self.input_broker is not None
        wake = threading.Event()
        wait_indicator = None
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ene-agent")
        active: Future | None = None
        self.input_broker.add_listener(wake.set)
        try:
            while not stop_event.is_set():
                if active is not None and active.done():
                    if active.result():
                        stop_event.set()
                        break
                    active = None
                item = self.input_broker.submission
                should_wait = (
                    active is None
                    and item is not None
                    and item.ready_at is not None
                    and not item.ready
                )
                if should_wait and wait_indicator is None:
                    wait_indicator = self.console.thinking(
                        label="Waiting",
                        countdown=max(0, item.ready_at - time.monotonic()),
                    )
                    wait_indicator.__enter__()
                elif not should_wait and wait_indicator is not None:
                    wait_indicator.__exit__(None, None, None)
                    wait_indicator = None
                if self.input_broker.pending:
                    if active is None and self.input_broker.ready:
                        active = executor.submit(self._run_round)
                    elif active is not None:
                        self._run_instant_command()
                wake.wait(0.1 if active is not None else 0.5)
                wake.clear()
        finally:
            if wait_indicator is not None:
                wait_indicator.__exit__(None, None, None)
            self.input_broker.remove_listener(wake.set)
            executor.shutdown(wait=True)

    def _startup_details(self) -> dict[str, str]:
        reasoning = self.profile.reasoning or "none"
        if reasoning != "none":
            reasoning += f" · {self.reasoning_effort} effort"
        return {
            "model": f"{self.provider_name}/{self.model}",
            "context": f"{self.context_length:,} tokens",
            "reasoning": reasoning,
            "persona": self.persona.name,
            "skills": self._skills_summary(),
            "workspace": self.work_dir,
        }

    def chat_loop(self, resumed_session_id: str | None = None):

        self.console.startup_panel(**self._startup_details())

        self._initialize_chat_session(resumed_session_id)

        _wd = self.tool_executor._work_dir or os.getcwd()
        self._refresh_slash_commands()
        terminal = TerminalInput(
            history_path=str(self._ene_dir() / "history"),
            work_dir=_wd,
            commands=self._slash_commands,
            system_message=self.console.system,
        )

        try:
            self._run_terminal_loop(terminal)
        finally:
            session_saved = False
            try:
                self.save_session(self._session_id)
                session_saved = True
            except Exception as e:
                self.console.warn(f"Could not save session before exit: {e}")
            self.close()
            self._print_token_summary(resume=self._session_id if session_saved else None)

    def close(self) -> None:
        """Release provider, process, skill, and change-tracking resources."""
        if self._closed:
            return
        self._closed = True
        try:
            if self.changes is not None:
                self.changes.close()
        finally:
            try:
                self.provider.close()
            finally:
                try:
                    self.tool_executor.shutdown_processes()
                finally:
                    self.tool_executor.shutdown_tool_resources(clear=True)

    def execute(self, query: str, *, manage_operation: bool = True):
        self.console.system(f"Executing query: {query}")
        t0 = time.time()

        if self._session_id is None:
            self._session_id = time.strftime("%Y%m%d_%H%M%S")

        # Strip @ prefix from file-path references
        query = _strip_at_marks(query)

        # bash command shortcut: !<command>
        if query.startswith("!"):
            bash_cmd = query[1:].strip()
            if bash_cmd:
                self._run_bash_command(bash_cmd)
            else:
                self.console.warn("Usage: !<shell command>")
            return None

        user_message = Message.user(query)
        self.context.add(user_message)

        self.console.rule()

        operation = self._operation("agent response") if manage_operation else nullcontext()
        with AgentCommandsMixin._round_timer(self):
            with operation:
                response = self.get_response()

        t1 = time.time()
        self.console.system(f"Execution time: {t1 - t0:.2f} seconds")
        self._print_token_summary()
        return response

    def _tool_compaction_summary(self) -> str:
        totals = self.tool_compaction_totals
        original = totals["original_chars"]
        retained = totals["retained_chars"]
        if not totals["calls"] or not original:
            return "none"
        saved = max(0, round((1 - retained / original) * 100))
        original_tokens = self.token_estimator.chars_to_tokens(original)
        retained_tokens = self.token_estimator.chars_to_tokens(retained)
        return (
            f"{totals['calls']} result(s), ~{original_tokens:,}→{retained_tokens:,} "
            f"tokens (-{saved}%)"
        )

    def _compaction_summary(self) -> str:
        totals = self.compaction_totals
        if not totals["count"]:
            return "none"
        before = totals["tokens_before"]
        after = totals["tokens_after"]
        saved = max(0, round((1 - after / before) * 100)) if before else 0
        return (
            f"{totals['count']}×, ~{before:,}→{after:,} tokens (-{saved}%) cumulative"
        )

    def _print_token_summary(self, resume: str | None = None):
        if resume is None:
            self.console.system(
                f"Total tokens used: {self.token_totals['total']} "
                f"(input: {self.token_totals['prompt']}, cached input: {self.token_totals['cached_prompt']}, "
                f"output: {self.token_totals['completion']}, reasoning: {self.token_totals['reasoning']})"
            )
            return
        self.console.session_end_panel(
            total=self.token_totals["total"],
            prompt=self.token_totals["prompt"],
            cached_prompt=self.token_totals["cached_prompt"],
            completion=self.token_totals["completion"],
            reasoning=self.token_totals["reasoning"],
            resume=resume,
        )
