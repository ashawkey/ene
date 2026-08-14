"""Tests for backend helpers that don't need a live API."""

import asyncio
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest
from prompt_toolkit.validation import ValidationError

import ene.backend as backend
from ene.backend import LLMAgent, _is_fatal_api_error
from ene.backend.commands import AgentCommandsMixin
from ene.context import CompactionState, ContextManager
from ene.messages import Message, ToolCall
from ene.providers import CompletionResult, ProviderError, ProviderUsage
from ene.utils.interrupt import RequestInterrupted
from ene.terminal import MessageValidator
from ene.utils.io import EventHub, InputBroker, UserSubmission


def test_reserve_session_id_is_atomic_across_agents(tmp_path):
    barrier = threading.Barrier(2)

    def reserve():
        agent = LLMAgent.__new__(LLMAgent)
        agent._session_id = None
        agent._session_timestamp = lambda: "20250102_030405"
        agent._sessions_dir = lambda: tmp_path / "sessions"
        (tmp_path / "sessions").mkdir(exist_ok=True)
        barrier.wait()
        return agent._reserve_session_id()

    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(lambda _: reserve(), range(2)))

    assert set(ids) == {"20250102_030405", "20250102_030405_2"}
    assert all((tmp_path / "sessions" / session_id).is_dir() for session_id in ids)


def test_initialize_chat_session_prunes_old_tool_result_artifacts(tmp_path):
    root = tmp_path / ".ene" / "tool-results"
    (root / "live").mkdir(parents=True)
    for index in range(21):
        (root / f"old-{index}").mkdir()

    agent = LLMAgent.__new__(LLMAgent)
    agent._session_id = None
    agent._session_store = NS(session_id="live", head_id="revision", exists=True)
    agent._session_revision_id = "revision"
    agent.changes = NS(session_id="live")
    agent.work_dir = str(tmp_path)
    agent.verbose = False
    agent.console = NS(warn=lambda _message: None, debug=lambda _message: None)

    agent._initialize_chat_session("live")

    assert (root / "live").is_dir()
    assert len([path for path in root.iterdir() if path.name != "live"]) == 20


def test_headless_agent_routes_console_prompts_through_broker(monkeypatch, tmp_path):
    from ene.ui import AgentConsole
    from ene.utils.io import PromptBroker

    events = EventHub()
    prompts = PromptBroker(events)
    console = AgentConsole(events=events, render_terminal=False)
    monkeypatch.setattr(backend, "create_provider", lambda *_args, **_kwargs: NS(close=lambda: None))

    agent = LLMAgent(
        model="test-model",
        api_key="",
        base_url="",
        provider_name="openai",
        console=console,
        events=events,
        prompt_broker=prompts,
        terminal_prompts=False,
        work_dir=str(tmp_path),
    )
    try:
        assert console.prompt_broker is prompts
    finally:
        agent.close()


def test_close_releases_resources_once():
    calls = []
    agent = LLMAgent.__new__(LLMAgent)
    agent._closed = False
    agent.changes = NS(close=lambda: calls.append("changes"))
    agent.provider = NS(close=lambda: calls.append("provider"))
    agent.tool_executor = NS(
        shutdown_processes=lambda: calls.append("processes"),
        shutdown_tool_resources=lambda clear=False: calls.append(("resources", clear)),
    )

    agent.close()
    agent.close()

    assert calls == ["changes", "provider", "processes", ("resources", True)]


class _StatusError(Exception):
    """Mimics an openai.APIStatusError instance carrying ``status_code``."""

    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_fatal_client_errors_are_not_retried(status):
    assert _is_fatal_api_error(_StatusError(status)) is True


@pytest.mark.parametrize("status", [408, 409, 425, 429, 500, 502, 503])
def test_transient_errors_are_retried(status):
    assert _is_fatal_api_error(_StatusError(status)) is False


def test_oauth_commands_use_current_provider():
    output = []

    class Console:
        def select(self, message, choices):
            return choices[0]

        def ask_text(self, message):
            return "code"

        def print(self, message, **kwargs):
            output.append(message)

        def system(self, message):
            output.append(message)

        def error(self, message):
            pytest.fail(message)

        def thinking(self, **kwargs):
            output.append(kwargs["label"])
            return nullcontext()

    class Provider:
        def __init__(self):
            self.logged_in = False

        def login(self, interaction):
            assert interaction.select("method", ["browser"]) == "browser"
            assert interaction.prompt("code") == "code"
            interaction.notify("open URL")
            self.logged_in = True

        def logout(self):
            self.logged_in = False

        def auth_status(self):
            return "logged in" if self.logged_in else "not logged in"

    agent = type("Agent", (AgentCommandsMixin,), {})()
    agent.console = Console()
    agent.provider = Provider()
    agent.provider_name = "openai-codex"
    agent.model_alias = "codex"
    agent.cancellation = None
    agent._operation = lambda label: nullcontext()

    agent._cmd_login("/login")
    agent._cmd_auth("/auth")
    agent._cmd_logout("/logout")

    assert "Authenticating" in output
    assert any("Logged in to openai-codex" in line for line in output)
    assert output[-1] == "Logged out of openai-codex."


@pytest.mark.parametrize(
    "query, instant",
    [
        ("/usage", True),
        ("/ps", True),
        ("/ps p-12345678", True),
        ("/context", True),
        ("/effort", True),
        ("/effort max", True),
        ("/name", True),
        ("/name webui", True),
        ("/wait 1h later", False),
        ("/model", True),        # bare form only lists
        ("/model gpt-5", False),  # switching swaps the provider mid-round
        ("/skills", True),
        ("/skills reload", False),
        ("/persona reload", False),
        ("/new", True),
        ("/compact", False),
        ("/recap", False),
        ("/export response.md", False),
        ("/continue", False),
        ("/rewind", False),
        ("/exit", False),
        ("/nonsense", True),     # a typo is reported straight away
    ],
)
def test_instant_command_classification(query, instant):
    agent = type("Agent", (AgentCommandsMixin,), {})()
    assert agent.is_instant_command(query) is instant


def _recap_agent(messages, provider):
    output = []
    warnings = []
    context = ContextManager("system")
    context.replace_messages(messages)
    agent = type("Agent", (AgentCommandsMixin,), {})()
    agent.context = context
    agent.provider = provider
    agent.model = "main-model"
    agent.cancellation = None
    agent._operation = lambda _label: nullcontext()
    agent.console = NS(
        print=lambda message, **kwargs: output.append((message, kwargs)),
        system=lambda message: output.append((message, {})),
        warn=warnings.append,
        thinking=lambda **kwargs: nullcontext(),
    )
    agent.token_totals = {
        "total": 0,
        "prompt": 0,
        "cached_prompt": 0,
        "completion": 0,
        "reasoning": 0,
    }
    agent._accumulate_usage = LLMAgent._accumulate_usage.__get__(agent)
    return agent, output, warnings


def test_recap_uses_only_user_requests_and_keeps_context_unchanged(monkeypatch):
    from ene.config import conf

    monkeypatch.delitem(conf, "recap_model", raising=False)
    requests = []
    usage = ProviderUsage(20, 5, 25)

    class Provider:
        def complete(self, request):
            requests.append(request)
            return CompletionResult(
                message=Message.assistant("  Build the recap feature.  "),
                usage=usage,
                finish_reason="stop",
            )

        def cancel(self):
            pass

    messages = [
        Message.user("build it"),
        Message.assistant("I will inspect the code"),
        Message.assistant("", tool_calls=[
            ToolCall(id="c1", name="read_file", arguments="{}"),
        ]),
        Message.tool("c1", "secret tool output"),
        Message.user("model-facing", display_content="/skill follow up"),
    ]
    agent, output, warnings = _recap_agent(messages, Provider())
    before = list(agent.context.messages)

    agent._handle_command("/recap")

    assert agent.context.messages == before
    assert warnings == []
    assert output[-1] == ("Recap: Build the recap feature.", {"markup": False})
    assert len(requests) == 1
    request = requests[0]
    prompt = request.messages[0].text
    assert "build it" in prompt
    assert "/skill follow up" in prompt
    assert "model-facing" not in prompt
    assert "secret tool output" not in prompt
    assert "inspect the code" not in prompt
    assert request.stream is False
    assert request.tools == []
    assert request.reasoning_effort == "low"
    assert request.max_output_tokens == 128
    assert agent.token_totals["total"] == 25


def test_recap_uses_compacted_original_request_and_bounds_input(monkeypatch):
    from ene.config import conf

    monkeypatch.delitem(conf, "recap_model", raising=False)
    provider = NS()
    agent, _, _ = _recap_agent([
        Message.user("[Previous conversation summary]\ninternal handoff"),
        Message.user("x" * 30_000),
        Message.assistant("irrelevant"),
        Message.user("latest request"),
    ], provider)
    agent.context.compaction_state = CompactionState(original_request="opening request")

    recap_input = agent._recap_input()

    assert recap_input.startswith("Opening request:\nopening request")
    assert "internal handoff" not in recap_input
    assert "latest request" in recap_input
    assert len(recap_input) <= 24_100


def test_recap_uses_configured_alias_and_closes_temporary_provider(monkeypatch):
    from ene.config import conf

    closed = []
    requests = []

    class Provider:
        def complete(self, request):
            requests.append(request)
            return CompletionResult(
                message=Message.assistant("Remember the task."),
                usage=None,
                finish_reason="stop",
            )

        def cancel(self):
            pass

        def close(self):
            closed.append(True)

    temporary = Provider()
    monkeypatch.setitem(conf, "recap_model", "fast")
    monkeypatch.setitem(conf, "openai", {
        "fast": {
            "provider": "openai",
            "model": "cheap-model",
            "api_key": "cheap-key",
            "base_url": "cheap-url",
        },
    })
    created = []
    monkeypatch.setattr(
        "ene.backend.commands.create_provider",
        lambda name, settings: created.append((name, settings)) or temporary,
    )
    active = NS(close=lambda: pytest.fail("active provider was closed"))
    agent, output, warnings = _recap_agent([
        Message.user("remember this"),
    ], active)

    agent._cmd_recap()

    assert warnings == []
    assert output[-1][0] == "Recap: Remember the task."
    assert created[0][0] == "openai"
    assert created[0][1].api_key == "cheap-key"
    assert requests[0].model == "cheap-model"
    assert closed == [True]


def test_compaction_uses_configured_summary_alias_and_closes_provider(monkeypatch):
    from ene.config import conf

    requests = []
    closed = []

    class Provider:
        def complete(self, request):
            requests.append(request)
            return CompletionResult(
                message=Message.assistant("condensed history"),
                usage=ProviderUsage(20, 5, 25),
                finish_reason="stop",
            )

        def cancel(self):
            pass

        def close(self):
            closed.append(True)

    temporary = Provider()
    created = []
    monkeypatch.setitem(conf, "summary_model", "fast")
    monkeypatch.setitem(conf, "openai", {
        "fast": {
            "provider": "openai",
            "model": "cheap-model",
            "api_key": "cheap-key",
            "base_url": "cheap-url",
        },
    })
    monkeypatch.setattr(
        "ene.backend.create_provider",
        lambda name, settings: created.append((name, settings)) or temporary,
    )

    agent = LLMAgent.__new__(LLMAgent)
    agent.provider = NS(close=lambda: pytest.fail("active provider was closed"))
    agent.model = "main-model"
    agent.cancellation = None
    agent._active_compaction_provider = None
    agent.token_totals = {
        "total": 0,
        "prompt": 0,
        "cached_prompt": 0,
        "completion": 0,
        "reasoning": 0,
    }

    assert agent._summarize("summarize this") == "condensed history"

    assert created[0][0] == "openai"
    assert created[0][1].api_key == "cheap-key"
    assert requests[0].model == "cheap-model"
    assert requests[0].reasoning_effort == "low"
    assert requests[0].max_output_tokens == 8_000
    assert agent.token_totals["total"] == 25
    assert closed == [True]
    assert agent._active_compaction_provider is None


def test_compaction_defaults_to_active_model(monkeypatch):
    from ene.config import conf

    monkeypatch.delitem(conf, "summary_model", raising=False)
    requests = []
    provider = NS(
        complete=lambda request: requests.append(request) or CompletionResult(
            message=Message.assistant("summary"),
            usage=None,
            finish_reason="stop",
        ),
    )
    agent = LLMAgent.__new__(LLMAgent)
    agent.provider = provider
    agent.model = "main-model"
    agent.cancellation = None
    agent._active_compaction_provider = None

    assert agent._summarize("summarize this") == "summary"
    assert requests[0].model == "main-model"


def test_recap_handles_empty_history_bad_usage_and_provider_failure(monkeypatch):
    from ene.config import conf

    monkeypatch.delitem(conf, "recap_model", raising=False)
    provider = NS(complete=lambda request: pytest.fail("provider should not run"))
    agent, output, warnings = _recap_agent([], provider)

    agent._cmd_recap()
    agent._cmd_recap("/recap extra")

    assert output == [("There is no conversation to recap yet.", {})]
    assert warnings == ["Usage: /recap"]

    class FailingProvider:
        def complete(self, request):
            raise RuntimeError("offline")

        def cancel(self):
            pass

    agent, _, warnings = _recap_agent([
        Message.user("task"),
    ], FailingProvider())
    agent._cmd_recap()
    assert warnings == ["Could not generate recap: offline"]


def test_export_writes_last_assistant_response_to_relative_path(tmp_path):
    output = []
    warnings = []
    agent = type("Agent", (AgentCommandsMixin,), {})()
    agent.work_dir = str(tmp_path)
    agent.context = NS(messages=[
        Message.assistant("earlier response"),
        Message.user("revise it"),
        Message.assistant("# Result\n\nFinal **answer**.\n"),
    ])
    agent.console = NS(system=output.append, warn=warnings.append)

    agent._handle_command("/export notes/result.md")

    exported = tmp_path / "notes" / "result.md"
    assert exported.read_text(encoding="utf-8") == "# Result\n\nFinal **answer**.\n"
    assert output == [f"Exported the last assistant response to {exported}."]
    assert warnings == []


def test_export_validates_path_and_requires_assistant_response(tmp_path):
    warnings = []
    agent = type("Agent", (AgentCommandsMixin,), {})()
    agent.work_dir = str(tmp_path)
    agent.context = NS(messages=[Message.user("hello")])
    agent.console = NS(system=lambda message: pytest.fail(message), warn=warnings.append)

    agent._cmd_export()
    agent._cmd_export("/export   ")
    agent._cmd_export("/export response.md")

    assert warnings == [
        "Usage: /export <path/filename>",
        "Usage: /export <path/filename>",
        "There is no assistant response to export yet.",
    ]
    assert not (tmp_path / "response.md").exists()


def test_context_detail_shows_untruncated_message_content():
    output = []
    warnings = []

    class Console:
        def print(self, message, **kwargs):
            output.append((str(message), kwargs))

        def warn(self, message):
            warnings.append(str(message))

    agent = type("Agent", (AgentCommandsMixin,), {})()
    agent.console = Console()
    agent.context = NS(messages=[Message.assistant(
        "full [literal] response\nsecond line",
        tool_calls=[ToolCall(
            id="call-1",
            name="write_file",
            arguments='{"content":"complete"}',
        )],
        reasoning_content="private [reasoning]",
    )])

    agent._handle_command("/context 0")

    assert ("full [literal] response\nsecond line", {"markup": False}) in output
    assert ("private [reasoning]", {"markup": False}) in output
    assert ('{"content":"complete"}', {"markup": False}) in output
    assert not warnings


def test_context_list_highlights_conversation_and_dims_tools():
    output = []

    class Console:
        def print(self, message, **kwargs):
            output.append(str(message))

    agent = type("Agent", (AgentCommandsMixin,), {})()
    agent.console = Console()
    agent.context = NS(messages=[
        Message.user("please [inspect] this"),
        Message.assistant("I will check"),
        Message.tool("call-1", "noisy [output]"),
    ])
    agent.token_estimator = NS(chars_to_tokens=lambda chars: chars // 4)
    agent.context_length = 1000

    agent._cmd_context()

    rendered = output[0]
    assert "[bold yellow]     user[/bold yellow]" in rendered
    assert "[yellow]please \\[inspect] this[/yellow]" in rendered
    assert "[bold white]assistant[/bold white]" in rendered
    assert "[white]I will check[/white]" in rendered
    assert "[bright_black]     tool[/bright_black]" in rendered
    assert "[bright_black](?)  noisy \\[output][/bright_black]" in rendered


def test_context_detail_validates_message_id():
    warnings = []
    agent = type("Agent", (AgentCommandsMixin,), {})()
    agent.console = NS(warn=warnings.append)
    agent.context = NS(messages=[Message.user("hello")])

    agent._cmd_context("/context nope")
    agent._cmd_context("/context 2")
    agent._cmd_context("/context 0 extra")

    assert warnings == [
        "Usage: /context [id]",
        "Context message #2 does not exist.",
        "Usage: /context [id]",
    ]


def test_process_status_callback_publishes_without_inspection(tmp_path):
    events = EventHub()
    agent = LLMAgent.__new__(LLMAgent)
    agent.events = events
    agent._process_status_sink = None
    agent.tool_executor = backend.ToolExecutor(work_dir=str(tmp_path))
    agent.tool_executor.set_process_status_callback(agent._process_status_changed)

    started = agent.tool_executor.execute(
        "start_process", {"command": "python -c 'import time; time.sleep(.2)'"}
    )
    try:
        assert _wait_until(lambda: agent.tool_executor.process_counts() == (0, 1))
        statuses = [event for event in events.after(0) if event.type == "process_status"]
        assert any(event.data["running"] == 1 for event in statuses)
        assert statuses[-1].data["finished"] == 1
        assert statuses[-1].data["text"] == ""
        assert started["process_id"]
    finally:
        agent.tool_executor.shutdown_processes()


def test_ps_lists_processes_and_shows_detail_tail():
    output = []
    calls = []

    class Console:
        def print(self, message):
            output.append(str(message))

        def system(self, message):
            output.append(str(message))

        def warn(self, message):
            output.append(str(message))

    process = {
        "process_id": "p-12345678",
        "pid": 42,
        "status": "running",
        "exit_code": None,
        "command": "python worker.py --long-option value",
        "label": "inspect parser failures",
        "cwd": "/tmp/work",
        "log_path": ".ene/processes/p-12345678.log",
        "log_tail": "ready\n",
    }
    agent = type("Agent", (AgentCommandsMixin,), {})()
    agent.console = Console()
    agent.tool_executor = NS(inspect_processes=lambda **kwargs: (
        calls.append(kwargs) or {"success": True, "processes": [process]}
    ))

    agent._cmd_ps("/ps")
    agent._cmd_ps("/ps p-12345678")

    assert calls == [
        {"process_id": None, "log_tail_chars": 0},
        {"process_id": "p-12345678", "log_tail_chars": 8000},
    ]
    assert "p-12345678" in output[0]
    assert "inspect parser failures" in output[0]
    assert any("label: inspect parser failures" in item for item in output)
    assert any("command: python worker.py --long-option value" in item for item in output)
    assert any("Recent output" in item and "ready" in item for item in output)


def test_effort_command_lists_choices_and_accepts_max():
    output = []
    errors = []
    agent = type("Agent", (AgentCommandsMixin,), {})()
    agent.console = NS(system=output.append, error=errors.append)
    agent.profile = NS(reasoning="anthropic")
    agent.reasoning_effort = "high"

    agent._cmd_effort("/effort")
    agent._cmd_effort("/effort max")

    assert output[0] == (
        "Reasoning: anthropic, effort: high. "
        "Available: none, minimal, low, medium, high, xhigh, max"
    )
    assert output[1] == "Reasoning effort set to max."
    assert agent.reasoning_effort == "max"
    assert errors == []


def test_slash_command_catalog_includes_skills_without_shadowing_builtins():
    agent = type("Agent", (AgentCommandsMixin,), {})()
    agent.skills = {
        "monitor-jobs": {"description": "Monitor jobs."},
        "usage": {"description": "Collides with a built-in."},
    }

    catalog = agent._slash_command_help()

    assert catalog["monitor-jobs"] == "Skill — Monitor jobs."
    assert catalog["usage"] == agent.COMMAND_HELP["usage"]


def test_skill_invocation_is_not_instant_and_cannot_shadow_builtin():
    agent = type("Agent", (AgentCommandsMixin,), {})()
    agent.skills = {
        "monitor-jobs": {"description": "Monitor jobs."},
        "usage": {"description": "Collides with a built-in."},
    }

    assert agent.is_instant_command("/monitor-jobs") is False
    assert agent.is_instant_command("/monitor-jobs training") is False
    assert agent.is_instant_command("/usage") is True


def _busy_agent(broker):
    """Agent stub standing in for one whose round runs on the worker thread."""
    dispatched = []
    echoed = []

    class Agent(AgentCommandsMixin):
        input_broker = broker
        console = NS(user_input=lambda text, **kwargs: echoed.append((text, kwargs)))

        def _run_command(self, query):
            dispatched.append(query)

    return Agent(), dispatched, echoed


def test_instant_command_runs_while_a_round_is_in_flight():
    broker = InputBroker(EventHub())
    broker.submit("/usage", source="web")
    agent, dispatched, echoed = _busy_agent(broker)

    assert LLMAgent._run_instant_command(agent) is True
    assert dispatched == ["/usage"]
    assert echoed[0][0] == "/usage"
    assert echoed[0][1]["source"] == "web"
    # Consumed, so the prompt stops advertising it as queued.
    assert broker.submission is None


@pytest.mark.parametrize("query", ["/wait 1h later", "steer the agent", "!git status"])
def test_non_instant_input_stays_queued_while_busy(query):
    broker = InputBroker(EventHub())
    submission = broker.submit(query)
    agent, dispatched, _ = _busy_agent(broker)

    assert LLMAgent._run_instant_command(agent) is False
    assert dispatched == []
    assert broker.submission == submission


def _wait_until(condition, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


class _ScriptedTerminal:
    """Terminal stub whose prompt returns lines the test feeds from outside."""

    def __init__(self):
        self.lines: queue.Queue[str] = queue.Queue()
        self.busy: list[bool] = []
        self.app = NS(invalidate=lambda: None, is_running=True)
        self.text = ""

    async def prompt_async(self, default: str = "") -> str:
        # A default means the loop handed a rejected draft back to the editor;
        # the script feeds one line at a time, so that must never happen.
        assert not default, f"unexpected draft returned to the editor: {default!r}"
        while True:
            try:
                return self.lines.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.005)

    def set_runtime_state(self, **kwargs) -> None:
        pass

    def set_busy(self, busy: bool) -> None:
        self.busy.append(busy)

    def set_status(self, status) -> None:
        pass


def test_message_validator_allows_instant_command_with_pending_input():
    validator = MessageValidator(lambda: True, lambda text: text == "/context")

    validator.validate(NS(text="/context"))
    with pytest.raises(ValidationError, match="already pending"):
        validator.validate(NS(text="follow up"))


def test_terminal_loop_answers_a_command_without_waiting_for_the_round(monkeypatch):
    """A command typed mid-round runs now; anything else waits its turn."""
    # Routing, not rendering, is under test: stdout patching needs a real console.
    monkeypatch.setattr(backend, "patch_stdout", lambda **kwargs: nullcontext())
    broker = InputBroker(EventHub())
    round_started = threading.Event()
    release_round = threading.Event()
    dispatched: list[str] = []

    class Agent(AgentCommandsMixin):
        input_broker = broker
        console = NS(user_input=lambda text, **kwargs: None, warn=lambda *a, **k: None)
        cancellation = None
        prompt_broker = None
        verbose = False
        _run_instant_command = LLMAgent._run_instant_command
        _run_round = LLMAgent._run_round

        def _process_next_submission(self) -> bool:
            text = broker.get_nowait().text
            if text == "exit":
                return True
            if text.startswith("/"):  # as _process_query routes a queued command
                self._run_command(text)
                return False
            round_started.set()
            release_round.wait(5)
            return False

        def _run_command(self, query: str) -> bool:
            dispatched.append(query)
            return False

    agent = Agent()
    terminal = _ScriptedTerminal()
    loop = threading.Thread(
        target=lambda: LLMAgent._run_terminal_loop(agent, terminal), daemon=True
    )
    loop.start()

    terminal.lines.put("do some work")
    assert round_started.wait(5)

    # Answered with the round still blocked on the worker thread.
    terminal.lines.put("/usage")
    assert _wait_until(lambda: dispatched == ["/usage"])
    assert not release_round.is_set()

    # A second instant command bypasses the occupied pending slot.
    queued = broker.submit("steer later")
    terminal.lines.put("/context")
    assert _wait_until(lambda: dispatched == ["/usage", "/context"])
    assert broker.submission == queued

    release_round.set()
    assert _wait_until(lambda: broker.submission is None)

    terminal.lines.put("exit")
    loop.join(timeout=5)
    assert not loop.is_alive()


def test_terminal_loop_survives_a_failing_round(monkeypatch):
    """One bad turn is reported and the session stays at the prompt.

    Regression: an exception escaping the worker thread propagated out of the
    UI loop, killing the whole chat session (and raising a second time from the
    loop's own cleanup).
    """
    monkeypatch.setattr(backend, "patch_stdout", lambda **kwargs: nullcontext())
    broker = InputBroker(EventHub())
    warnings: list[str] = []

    class Agent(AgentCommandsMixin):
        input_broker = broker
        console = NS(
            user_input=lambda text, **kwargs: None,
            warn=lambda msg, **kwargs: warnings.append(msg),
        )
        cancellation = None
        prompt_broker = None
        verbose = False
        _run_instant_command = LLMAgent._run_instant_command
        _run_round = LLMAgent._run_round

        def _process_next_submission(self) -> bool:
            if broker.get_nowait().text == "exit":
                return True
            raise RuntimeError("round blew up")

        def _run_command(self, query: str) -> bool:
            return False

    terminal = _ScriptedTerminal()
    loop = threading.Thread(
        target=lambda: LLMAgent._run_terminal_loop(Agent(), terminal), daemon=True
    )
    loop.start()

    terminal.lines.put("do some work")
    assert _wait_until(lambda: any("round blew up" in w for w in warnings))
    assert loop.is_alive()  # the session outlived the failure

    terminal.lines.put("exit")  # and still accepts input
    loop.join(timeout=5)
    assert not loop.is_alive()


def _skill_invocation_agent(tmp_path, name="general-skill"):
    skill_dir = tmp_path / ".ene" / "skills" / name
    skill_dir.mkdir(parents=True)
    body = "General instructions."
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use for tests.\n---\n{body}\n",
        encoding="utf-8",
    )
    agent = object.__new__(LLMAgent)
    agent.skills = backend.discover_skills(tmp_path)
    agent.context = ContextManager("system")
    agent.tool_executor = backend.ToolExecutor(work_dir=str(tmp_path), skills=agent.skills)
    agent.console = NS(
        rule=lambda *a, **k: None,
        user_input=lambda *a, **k: None,
        warn=lambda *a, **k: None,
        system=lambda *a, **k: None,
        reset_timeline=lambda: None,
    )
    agent.round_id = 0
    agent._session_id = "test"
    agent._session_revision_id = None
    agent._compaction_floor_tokens = None
    agent._pending_images = []
    agent._last_interrupted = False
    agent.verbose = False
    agent._operation = lambda _label: nullcontext()
    agent.save_session = lambda *a, **k: None
    agent.get_response = lambda: None
    return agent, body


@pytest.mark.parametrize("query", ["/resume", "/resume saved-session", "/rewind"])
def test_web_cannot_resume_or_rewind(query, tmp_path):
    agent, _ = _skill_invocation_agent(tmp_path)
    warnings = []
    dispatched = []
    agent.console.warn = warnings.append
    agent._run_command = dispatched.append

    assert agent._process_query(UserSubmission(query, "web", "s1")) is False

    command = query.split()[0]
    assert warnings == [f"{command} is only available from an attached terminal."]
    assert dispatched == []
    assert agent.context.messages == []
    assert agent.round_id == 0


def test_explicit_skill_invocation_without_task_asks_model_not_to_infer(tmp_path):
    agent, body = _skill_invocation_agent(tmp_path)

    agent._process_query(UserSubmission("/general-skill", "terminal", "s1"))

    call, result, message = agent.context.messages
    tool_call = call.tool_calls[0]
    assert call.role == "assistant"
    assert tool_call.name == "load_skill"
    assert tool_call.arguments == '{"name": "general-skill"}'
    assert result.role == "tool"
    assert result.tool_call_id == tool_call.id
    assert body in result.text
    assert message.display_content == "/general-skill"
    assert body not in message.text
    assert "Default invocation" in message.text
    assert "do not infer or start a task" in message.text
    state = CompactionState().absorb(agent.context.messages)
    assert state.original_request == message.text
    assert state.skills == ("general-skill",)
    assert agent.round_id == 1


def test_explicit_skill_invocation_with_task_and_when_already_loaded(tmp_path):
    agent, body = _skill_invocation_agent(tmp_path)
    system_messages = []
    agent.console.system = system_messages.append

    agent._process_query(UserSubmission("/general-skill first task", "web", "s1"))
    agent._process_query(UserSubmission("/general-skill second task", "web", "s2"))

    first_call, first_result, first, second_call, second_result, second = (
        agent.context.messages
    )
    assert first_call.tool_calls[0].name == "load_skill"
    assert body in first_result.text
    assert first.text == "first task"
    assert first.display_content == "/general-skill first task"
    assert second_call.tool_calls[0].name == "load_skill"
    assert body in second_result.text
    assert second.text == "second task"
    assert second.display_content == "/general-skill second task"
    state = CompactionState().absorb(agent.context.messages)
    assert state.original_request == "first task"
    assert state.skills == ("general-skill",)
    assert agent.round_id == 2
    assert system_messages == [
        "Loaded skill 'general-skill' into context. It will guide the next response.",
        "Loaded skill 'general-skill' into context. It will guide the next response.",
    ]


def test_manual_skill_load_records_tool_pair_without_user_message(tmp_path):
    agent, body = _skill_invocation_agent(tmp_path)
    agent.console.system = lambda *a, **k: None

    agent._cmd_skills("/skills general-skill")

    call, result = agent.context.messages
    tool_call = call.tool_calls[0]
    assert call.role == "assistant"
    assert tool_call.name == "load_skill"
    assert result.role == "tool"
    assert result.tool_call_id == tool_call.id
    assert body in result.text
    assert not any(message.is_user for message in agent.context.messages)
    assert agent.round_id == 0


def test_cancelled_skill_invocation_restores_skill_state(tmp_path):
    agent, _ = _skill_invocation_agent(tmp_path)
    agent.events = EventHub()
    agent.console.system = lambda *a, **k: None
    agent._set_rewind_draft = lambda *a, **k: None

    def interrupt():
        agent._last_interrupted = True
        agent._interrupt_reverts_prompt = True

    agent.get_response = interrupt
    agent._process_query(UserSubmission("/general-skill", "terminal", "s1"))

    assert agent.context.messages == []
    assert agent.tool_executor._loaded_skills == set()
    assert agent.tool_executor._skill_loads == {}


def test_cancelled_initial_request_restores_context_and_message_draft():
    context = ContextManager("system")
    context.add(Message.user("u1"))
    context.add(Message.assistant("a1"))
    messages_before = list(context.messages)
    state_before = CompactionState(original_request="u1")
    context.compaction_state = state_before

    events = EventHub()
    resets = []
    saved = []
    console = NS(
        rule=lambda: None,
        user_input=lambda *args, **kwargs: None,
        response=lambda *args, **kwargs: None,
        system=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
        warn=lambda *args, **kwargs: None,
        reset_timeline=lambda: resets.append(True),
    )

    agent = object.__new__(LLMAgent)
    agent.context = context
    agent.events = events
    agent.console = console
    agent.round_id = 1
    agent._session_id = "test"
    agent._session_revision_id = "before-round"
    agent._compaction_floor_tokens = 123
    agent._pending_images = []
    agent._last_interrupted = False
    agent.verbose = False
    agent.tool_executor = NS()
    agent._operation = lambda _label: nullcontext()
    agent.save_session = lambda *args, **kwargs: saved.append(
        (list(context.messages), agent.round_id, agent._session_revision_id, kwargs)
    )

    def cancel_after_context_management():
        context.replace_messages([Message.user("compacted u2")])
        context.compaction_state = CompactionState(original_request="u2")
        agent._session_revision_id = "cancelled-round-snapshot"
        agent._compaction_floor_tokens = 999
        raise RequestInterrupted()

    agent.call_api = cancel_after_context_management

    agent._process_query(
        UserSubmission("u2 @config.py", "terminal", "submission-2")
    )

    assert context.messages == messages_before
    assert context.compaction_state == state_before
    assert agent.round_id == 1
    assert agent._session_revision_id == "before-round"
    assert agent._compaction_floor_tokens == 123
    assert agent._rewind_draft == "u2 @config.py"
    assert resets == [True]
    assert saved == [(messages_before, 1, "before-round", {"reason": "round"})]
    draft_events = [event for event in events.after(0) if event.type == "draft_set"]
    assert [event.data["text"] for event in draft_events] == ["u2 @config.py"]


def test_failed_initial_request_restores_context_and_message_draft():
    context = ContextManager("system")
    context.add(Message.user("u1"))
    context.add(Message.assistant("a1"))
    messages_before = list(context.messages)
    state_before = CompactionState(original_request="u1")
    context.compaction_state = state_before

    events = EventHub()
    resets = []
    systems = []
    errors = []
    saved = []
    console = NS(
        rule=lambda: None,
        user_input=lambda *args, **kwargs: None,
        response=lambda *args, **kwargs: None,
        system=lambda text, **kwargs: systems.append(text),
        error=lambda text, **kwargs: errors.append(text),
        warn=lambda *args, **kwargs: None,
        reset_timeline=lambda: resets.append(True),
    )

    agent = object.__new__(LLMAgent)
    agent.context = context
    agent.events = events
    agent.console = console
    agent.round_id = 1
    agent._session_id = "test"
    agent._session_revision_id = "before-round"
    agent._compaction_floor_tokens = 123
    agent._pending_images = []
    agent._last_interrupted = False
    agent.verbose = False
    agent.tool_executor = NS()
    agent._operation = lambda _label: nullcontext()
    agent.save_session = lambda *args, **kwargs: saved.append(
        (list(context.messages), agent.round_id, agent._session_revision_id, kwargs)
    )

    def fail_after_context_management():
        context.replace_messages([Message.user("compacted u2")])
        context.compaction_state = CompactionState(original_request="u2")
        agent._session_revision_id = "failed-round-snapshot"
        agent._compaction_floor_tokens = 999
        raise RuntimeError("bad request")

    agent.call_api = fail_after_context_management
    agent._process_query(UserSubmission("u2 @config.py", "web", "submission-2"))

    assert context.messages == messages_before
    assert context.compaction_state == state_before
    assert agent.round_id == 1
    assert agent._session_revision_id == "before-round"
    assert agent._compaction_floor_tokens == 123
    assert agent._rewind_draft == "u2 @config.py"
    assert resets == [True]
    assert errors == ["API call failed: bad request", "API call failed: bad request"]
    assert systems[-1] == "Turn failed. Message restored to the editor."
    assert saved == [(messages_before, 1, "before-round", {"reason": "round"})]
    draft_events = [event for event in events.after(0) if event.type == "draft_set"]
    assert [event.data["text"] for event in draft_events] == ["u2 @config.py"]


def test_cancelled_exec_preserves_its_partial_result_in_round_context(tmp_path):
    context = ContextManager("system")
    context.add(Message.user("u1"))
    context.add(Message.assistant("a1"))
    messages_before = list(context.messages)
    tool_call = ToolCall(
        id="call-1",
        name="exec_command",
        arguments='{"command": "slow"}',
    )

    events = EventHub()
    resets = []
    systems = []
    saved = []
    console = NS(
        rule=lambda: None,
        user_input=lambda *args, **kwargs: None,
        response=lambda *args, **kwargs: None,
        system=lambda text, **kwargs: systems.append(text),
        error=lambda *args, **kwargs: None,
        warn=lambda *args, **kwargs: None,
        tool_result=lambda *args, **kwargs: None,
        thinking=lambda *args, **kwargs: nullcontext(),
        reset_timeline=lambda: resets.append(True),
    )

    agent = object.__new__(LLMAgent)
    agent.context = context
    agent.events = events
    agent.console = console
    agent.round_id = 1
    agent._session_id = "test"
    agent._session_revision_id = "before-round"
    agent._compaction_floor_tokens = None
    agent._pending_images = []
    agent._last_interrupted = False
    agent.verbose = False
    agent.stream = False
    agent.input_broker = None
    agent.cancellation = None
    agent.context_length = 16_000
    agent.token_estimator = NS(chars_per_token=3.3)
    agent.work_dir = str(tmp_path)
    agent.tool_compaction_totals = {
        "calls": 0,
        "original_chars": 0,
        "retained_chars": 0,
    }
    agent.tool_executor = NS(
        execute=lambda *args: {
            "stdout": "partial output\n",
            "exit_code": 143,
            "success": False,
            "streamed": True,
            "interrupted": True,
            "error": "Command was interrupted by user.",
        },
    )
    agent._operation = lambda _label: nullcontext()
    agent.save_session = lambda *args, **kwargs: saved.append(list(context.messages))

    def call_api():
        message = Message.assistant(None, tool_calls=[tool_call])
        context.add(message)
        return message

    agent.call_api = call_api
    agent._process_query(UserSubmission("run it", "terminal", "submission-2"))

    expected = messages_before + [
        Message.user("run it"),
        Message.assistant(None, tool_calls=[tool_call]),
        Message.tool(
            "call-1",
            "partial output\n"
            "[exit_code: 143, interrupted: true, timed_out: false]",
        ),
    ]
    assert context.messages == expected
    assert agent.round_id == 2
    assert not hasattr(agent, "_rewind_draft")
    assert resets == []
    assert saved == [expected]
    assert systems[-1:] == ["Turn interrupted."]
    assert not [event for event in events.after(0) if event.type == "draft_set"]


def _continue_agent(messages):
    warnings = []
    calls = []
    saved = []
    agent = type("Agent", (AgentCommandsMixin,), {})()
    agent.context = ContextManager("system")
    agent.context.replace_messages(messages)
    agent.console = NS(
        warn=lambda message: warnings.append(message),
        rule=lambda: calls.append("rule"),
    )
    agent._operation = lambda label: nullcontext()
    agent.get_response = lambda: calls.append("response")
    agent._session_id = "test"
    agent.save_session = lambda *args, **kwargs: saved.append((args, kwargs))
    return agent, warnings, calls, saved


def test_name_command_shows_sets_and_persists_name():
    messages = []
    saved = []
    changed = []
    agent = object.__new__(LLMAgent)
    agent.console = NS(system=messages.append, error=messages.append)
    agent.session_name = ""
    agent._session_id = "conversation"
    agent._session_name_changed = lambda name: changed.append(name) or name.strip()
    agent._session_store = NS(
        session_id="conversation",
        rename=lambda name: saved.append(name),
    )

    agent._cmd_name("/name")
    agent._cmd_name("/name useful work")

    assert messages == ["This session is unnamed.", "Session named 'useful work'."]
    assert agent.session_name == "useful work"
    assert changed == ["useful work"]
    assert saved == ["useful work"]


def test_continue_resumes_after_tool_result_without_adding_user_message():
    messages = [
        Message.user("do it"),
        Message.assistant(None, tool_calls=[
            ToolCall(id="call-1", name="load_skill", arguments='{"name":"lean"}'),
        ]),
        Message.tool("call-1", "loaded"),
    ]
    agent, warnings, calls, saved = _continue_agent(messages)

    agent._cmd_continue()

    assert agent.context.messages == messages
    assert warnings == []
    assert calls == ["rule", "response"]
    assert saved == [(('test',), {"reason": "round"})]


def test_continue_warns_when_round_is_complete():
    messages = [
        Message.user("hello"),
        Message.assistant("done"),
    ]
    agent, warnings, calls, saved = _continue_agent(messages)

    agent._cmd_continue()

    assert warnings == ["The last round is already complete; nothing to continue."]
    assert calls == []
    assert saved == []


def test_continue_resumes_after_empty_assistant_message():
    messages = [
        Message.user("hello"),
        Message.assistant(""),
    ]
    agent, warnings, calls, saved = _continue_agent(messages)

    agent._cmd_continue()

    assert warnings == []
    assert calls == ["rule", "response"]
    assert saved == [(('test',), {"reason": "round"})]


def test_continue_rejects_partial_tool_results():
    messages = [
        Message.assistant(None, tool_calls=[
            ToolCall(id="call-1", name="one", arguments="{}"),
            ToolCall(id="call-2", name="two", arguments="{}"),
        ]),
        Message.tool("call-1", "done"),
    ]
    agent, warnings, calls, saved = _continue_agent(messages)

    agent._cmd_continue()

    assert warnings == [
        "The last assistant message has unresolved tool calls; cannot continue safely."
    ]
    assert calls == []
    assert saved == []


def test_provider_retry_classification_overrides_http_status():
    assert _is_fatal_api_error(
        ProviderError("subscription limit", status_code=429, retryable=False)
    ) is True
    assert _is_fatal_api_error(
        ProviderError("temporary", status_code=400, retryable=True)
    ) is False
