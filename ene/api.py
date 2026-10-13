"""Public Python API for one-shot ene agent runs."""

from __future__ import annotations

import math
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from ene.config import CONFIG_PATH, conf
from ene.backend import DEFAULT_NOTIFY_TIMEOUT, LLMAgent
from ene.models import ReasoningEffort, resolve_model_alias
from ene.providers import provider_names
from ene.ui import AgentConsole
from ene.utils.interrupt import TurnOutcome


@dataclass(frozen=True)
class AgentRunResult:
    """Result of a completed :func:`run_agent` invocation."""

    response: str | None
    outcome: TurnOutcome
    token_usage: dict[str, int]
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.outcome == TurnOutcome.COMPLETED


def run_agent(
    task: str,
    *,
    model_alias: str | None = None,
    persona: str | None = None,
    work_dir: str | Path | None = None,
    reasoning_effort: ReasoningEffort | None = None,
    stream: bool = False,
    verbose: bool = False,
    quiet: bool = True,
    console: AgentConsole | None = None,
    notify_timeout: float | None = None,
) -> AgentRunResult:
    """Run one independent, non-interactive agent task.

    ``model_alias`` selects an entry under ``openai`` in the loaded ene
    configuration; omitting it selects the first configured model. The run has
    a fresh conversation, does not create an interactive session or rewind
    history, and always releases provider, process, and skill resources before
    returning.

    Progress output is suppressed by default. Pass ``quiet=False`` to observe
    normal output, optionally through a custom ``console``. Configuration and
    construction errors are raised; provider failures and interruptions are
    represented by ``AgentRunResult.outcome``. Responses that remain unfinished
    after automatic continuations, or truncate during a tool call, return
    ``FAILED`` with an error explaining why; any final partial text is retained
    in ``response``.

    The run stays open while a background process or wakeup can still notify
    the agent. After ``notify_timeout`` seconds without a notification
    (default: ``notify_timeout`` in the configuration, else one hour), the
    remaining background work is stopped and the agent gives its final answer.
    """
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task must be a non-empty string")
    if notify_timeout is None:
        configured = conf.get("notify_timeout")
        # An invalid configured value falls back like malformed configuration.
        notify_timeout = (
            float(configured) if _valid_timeout(configured) else DEFAULT_NOTIFY_TIMEOUT
        )
    elif not _valid_timeout(notify_timeout):
        raise ValueError("notify_timeout must be a finite number greater than zero")

    model_configs = conf.get("openai", {})
    if not isinstance(model_configs, dict) or not model_configs:
        raise ValueError(f"No models found in config: {CONFIG_PATH}")

    alias = resolve_model_alias(model_alias or next(iter(model_configs)), model_configs)

    model_conf = model_configs[alias]
    provider_name = model_conf.get("provider", "openai")
    if provider_name not in provider_names():
        available = ", ".join(provider_names())
        raise ValueError(f"Unknown provider '{provider_name}'. Available: {available}")

    run_console = console or AgentConsole()
    agent = LLMAgent(
        model=model_conf.get("model", alias),
        api_key=model_conf.get("api_key", ""),
        base_url=model_conf.get("base_url", ""),
        api=model_conf.get("api", "chat_completions"),
        provider_name=provider_name,
        model_alias=alias,
        verbose=verbose,
        stream=stream,
        reasoning_effort=reasoning_effort
        or model_conf.get("reasoning_effort", "high"),
        context_length=model_conf.get("context_length"),
        max_output_tokens=model_conf.get("max_output_tokens"),
        persona=persona,
        exec_mode=True,
        work_dir=str(work_dir) if work_dir is not None else None,
        console=run_console,
    )

    try:
        output_context = run_console.suppressed() if quiet else nullcontext()
        with output_context:
            response = agent.execute(task, notify_timeout=float(notify_timeout))
        return AgentRunResult(
            response=response,
            outcome=agent._last_turn_outcome,
            token_usage=dict(agent.token_totals),
            error=agent._last_error,
        )
    finally:
        agent.close()


def _valid_timeout(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


__all__ = ["AgentRunResult", "TurnOutcome", "run_agent"]
