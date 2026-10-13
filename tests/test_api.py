"""Tests for the public one-shot Python API."""

from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest

from ene.config import conf
from ene import AgentRunResult, TurnOutcome, run_agent
from ene import api
from ene.backend.inbox import FINAL_DELIVERY_NOTE
from ene.messages import Message, ToolCall
from ene.providers import CompletionResult


class _Console:
    def __init__(self):
        self.suppressed_calls = 0

    def suppressed(self):
        self.suppressed_calls += 1
        return nullcontext()


@pytest.fixture
def model_config(monkeypatch):
    monkeypatch.setitem(conf, "openai", {
        "test": {
            "model": "test-model",
            "api_key": "key",
            "base_url": "url",
            "provider": "openai",
            "reasoning_effort": "medium",
            "context_length": 200_000,
            "max_output_tokens": 16_000,
        }
    })


@pytest.mark.parametrize("api_mode", ["chat_completions", "responses"])
@pytest.mark.parametrize("alias_input", ["test", "tes"])
def test_run_agent_constructs_exec_agent_and_closes(monkeypatch, model_config, tmp_path, api_mode, alias_input):
    monkeypatch.setitem(conf["openai"]["test"], "api", api_mode)
    created = []

    class FakeAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self._last_turn_outcome = TurnOutcome.COMPLETED
            self._last_error = None
            self.token_totals = {"total": 12, "prompt": 8, "completion": 4}
            self.closed = False
            created.append(self)

        def execute(self, task, *, notify_timeout):
            assert task == "inspect this"
            return "done"

        def close(self):
            self.closed = True

    console = _Console()
    monkeypatch.setattr(api, "LLMAgent", FakeAgent)

    result = run_agent(
        "inspect this",
        model_alias=alias_input,
        persona="coder",
        work_dir=tmp_path,
        console=console,
    )

    assert isinstance(result, AgentRunResult)
    assert result.success
    assert result.response == "done"
    assert result.outcome == TurnOutcome.COMPLETED
    assert result.token_usage == {"total": 12, "prompt": 8, "completion": 4}
    assert result.error is None
    assert console.suppressed_calls == 1
    assert created[0].closed
    assert created[0].kwargs["exec_mode"] is True
    assert created[0].kwargs["api"] == api_mode
    assert created[0].kwargs["model_alias"] == "test"
    assert created[0].kwargs["persona"] == "coder"
    assert created[0].kwargs["work_dir"] == str(tmp_path)
    assert created[0].kwargs["stream"] is False
    assert created[0].kwargs["reasoning_effort"] == "medium"
    assert created[0].kwargs["context_length"] == 200_000
    assert created[0].kwargs["max_output_tokens"] == 16_000


def test_run_agent_returns_failed_outcome(monkeypatch, model_config):
    class FakeAgent:
        def __init__(self, **kwargs):
            self._last_turn_outcome = TurnOutcome.FAILED
            self._last_error = "request failed"
            self.token_totals = {"total": 0}
            self.closed = False

        def execute(self, task, *, notify_timeout):
            return None

        def close(self):
            self.closed = True

    monkeypatch.setattr(api, "LLMAgent", FakeAgent)

    result = run_agent("try once", model_alias="test", quiet=False)

    assert not result.success
    assert result.outcome == TurnOutcome.FAILED
    assert result.error == "request failed"


@pytest.mark.parametrize("case", ["empty", "partial", "tool"])
def test_run_agent_reports_unfinished_responses_as_failed(monkeypatch, model_config, tmp_path, case):
    calls = []
    closed = []

    def complete(request):
        calls.append(request)
        if case == "empty":
            return CompletionResult(Message.assistant(""), None, "stop")
        tool_calls = [ToolCall("cut", "write_file", '{"file":')] if case == "tool" else None
        return CompletionResult(Message.assistant("partial", tool_calls=tool_calls), None, "length")

    provider = NS(complete=complete, close=lambda: closed.append(True), cancel=lambda: None)
    monkeypatch.setattr("ene.backend.create_provider", lambda *_args: provider)

    result = run_agent("write a file", model_alias="test", work_dir=tmp_path)

    assert result.outcome == TurnOutcome.FAILED
    assert result.success is False
    assert result.response == (None if case == "empty" else "partial")
    assert ("truncated during a tool call" if case == "tool" else "still unfinished") in result.error
    assert len(calls) == (1 if case == "tool" else api.LLMAgent.MAX_AUTO_CONTINUES + 1)
    assert closed == [True]


def test_run_agent_closes_when_execution_raises(monkeypatch, model_config):
    created = []

    class FakeAgent:
        def __init__(self, **kwargs):
            self.closed = False
            created.append(self)

        def execute(self, task, *, notify_timeout):
            raise LookupError("boom")

        def close(self):
            self.closed = True

    monkeypatch.setattr(api, "LLMAgent", FakeAgent)

    with pytest.raises(LookupError, match="boom"):
        run_agent("fail", model_alias="test", quiet=False)

    assert created[0].closed


def test_run_agent_validates_task_and_model(model_config):
    with pytest.raises(ValueError, match="non-empty"):
        run_agent("  ", model_alias="test")
    with pytest.raises(ValueError, match="Model 'missing' not found"):
        run_agent("task", model_alias="missing")


def test_run_agent_rejects_ambiguous_alias_before_constructing_agent(monkeypatch, model_config):
    monkeypatch.setitem(conf["openai"], "test-other", {"model": "other"})
    monkeypatch.setattr(api, "LLMAgent", lambda **kwargs: pytest.fail("constructed an ambiguous model"))
    with pytest.raises(ValueError, match="Ambiguous model 'tes'. Matches: test, test-other"):
        run_agent("task", model_alias="tes")


def test_run_agent_stays_open_until_process_notification(monkeypatch, model_config, tmp_path):
    requests = []
    start = ToolCall(
        "start", "start_process",
        # The delay keeps the exit after this turn, so it must wake a new round.
        '{"command": "python -c \\"import time; time.sleep(0.5); print(42)\\"", '
        '"label": "answer"}',
    )
    replies = [
        CompletionResult(Message.assistant(None, tool_calls=[start]), None, "tool_calls"),
        CompletionResult(Message.assistant("Started; waiting."), None, "stop"),
        CompletionResult(Message.assistant("The job printed 42."), None, "stop"),
    ]

    def complete(request):
        requests.append(request)
        return replies.pop(0)

    provider = NS(complete=complete, close=lambda: None, cancel=lambda: None)
    monkeypatch.setattr("ene.backend.create_provider", lambda *_args: provider)

    result = run_agent("run the job", model_alias="test", work_dir=tmp_path)

    assert result.success
    assert result.response == "The job printed 42."
    inbox = requests[-1].messages[-1].text
    assert '<event source="process:1 answer"' in inbox
    assert "exited with code 0" in inbox and "Last output line: 42" in inbox


def test_run_agent_returns_when_nothing_can_notify(monkeypatch, model_config, tmp_path):
    calls = []

    def complete(request):
        calls.append(request)
        return CompletionResult(Message.assistant("done"), None, "stop")

    provider = NS(complete=complete, close=lambda: None, cancel=lambda: None)
    monkeypatch.setattr("ene.backend.create_provider", lambda *_args: provider)

    result = run_agent("answer", model_alias="test", work_dir=tmp_path)

    assert result.response == "done" and len(calls) == 1


def test_run_agent_final_delivery_asks_for_the_complete_answer(monkeypatch, model_config, tmp_path):
    requests = []
    start = ToolCall(
        "start", "start_process",
        '{"command": "python -c \\"import time; time.sleep(0.5)\\"", "label": "job"}',
    )
    replies = [
        CompletionResult(Message.assistant(None, tool_calls=[start]), None, "tool_calls"),
        CompletionResult(Message.assistant("The answer is 42."), None, "stop"),
        # A notification round without a reply keeps the earlier answer.
        CompletionResult(Message.assistant(""), None, "content_filter"),
    ]

    def complete(request):
        requests.append(request)
        return replies.pop(0)

    provider = NS(complete=complete, close=lambda: None, cancel=lambda: None)
    monkeypatch.setattr("ene.backend.create_provider", lambda *_args: provider)

    result = run_agent("answer after the job", model_alias="test", work_dir=tmp_path)

    inbox = requests[2].messages[-1]
    assert inbox.is_inbox and FINAL_DELIVERY_NOTE in inbox.text
    assert result.response == "The answer is 42."


def test_run_agent_resolves_notify_timeout(monkeypatch, model_config):
    timeouts = []

    class Agent:
        _last_turn_outcome = TurnOutcome.COMPLETED
        _last_error = None
        token_totals = {}

        def __init__(self, **_kwargs):
            pass

        def execute(self, _task, *, notify_timeout):
            timeouts.append(notify_timeout)
            return "done"

        def close(self):
            pass

    monkeypatch.setattr(api, "LLMAgent", Agent)
    run_agent("task", model_alias="test")
    monkeypatch.setitem(conf, "notify_timeout", 90)
    run_agent("task", model_alias="test")
    run_agent("task", model_alias="test", notify_timeout=5)
    # An invalid configured value falls back like malformed configuration.
    monkeypatch.setitem(conf, "notify_timeout", "soon")
    run_agent("task", model_alias="test")
    assert timeouts == [api.DEFAULT_NOTIFY_TIMEOUT, 90.0, 5.0, api.DEFAULT_NOTIFY_TIMEOUT]

    for invalid in (0, -1, float("inf"), True, "5"):
        with pytest.raises(ValueError, match="notify_timeout"):
            run_agent("task", model_alias="test", notify_timeout=invalid)
