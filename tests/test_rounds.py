"""Per-round statistics recorded by the agent and persisted with the session."""

import json
from types import SimpleNamespace as NS

import ene.backend as backend
from ene.backend import LLMAgent
from ene.messages import Message, ToolCall
from ene.providers import CompletionResult, ProviderUsage
from ene.session_store import SessionStore
from ene.backend.rounds import OutputRateMeter
from ene.ui import AgentConsole
from ene.utils.io import EventHub


def usage(prompt, completion, cached=0):
    return ProviderUsage(prompt, completion, prompt + completion, cached)


def make_agent(monkeypatch, tmp_path, replies, *, events=None):
    monkeypatch.chdir(tmp_path)  # sessions live under the cwd's .ene/
    def complete(request):
        message, used = replies.pop(0)
        return CompletionResult(message, used, "stop")

    provider = NS(complete=complete, close=lambda: None, cancel=lambda: None,
                  context_length=lambda model: None)
    monkeypatch.setattr(backend, "create_provider", lambda *_a, **_k: provider)
    agent = LLMAgent(
        model="test-model", api_key="", base_url="", provider_name="openai",
        console=AgentConsole(render_terminal=False), events=events,
        terminal_prompts=False, stream=False, work_dir=str(tmp_path),
    )
    return agent


def run(agent, text):
    agent.input_broker.submit(text, source="terminal")
    agent._process_next_submission()


def test_rounds_record_tokens_tools_and_reach_clients(monkeypatch, tmp_path):
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")
    events = EventHub()
    read = ToolCall(id="c1", name="read_file", arguments=json.dumps({"file": "notes.txt"}))
    agent = make_agent(monkeypatch, tmp_path, [
        (Message.assistant(None, tool_calls=[read]), usage(1000, 50, 800)),
        (Message.assistant("Read it."), usage(1200, 30, 1000)),
        (Message.assistant("Second."), usage(1300, 20, 1200)),
    ], events=events)
    try:
        agent._initialize_chat_session()
        run(agent, "read the notes")
        run(agent, "and again")

        first, second = agent.round_history
        assert (first["prompt"], first["kind"]) == ("read the notes", "user")
        assert (first["input_tokens"], first["cached_tokens"], first["output_tokens"]) == (2200, 1800, 80)
        assert (first["tool_calls"], first["tool_failures"]) == (1, 0)
        assert second["input_tokens"] == 1300 and second["tool_calls"] == 0
        assert second["index"] == first["index"] + 1
        assert second["duration"] >= 0 and not second["cancelled"]

        published = [e.data for e in events.snapshot() if e.type == "round_stats"]
        assert [item["id"] for item in published] == [first["id"], second["id"]]
    finally:
        agent.close()


def test_round_history_survives_resume_and_follows_rewind(monkeypatch, tmp_path):
    agent = make_agent(monkeypatch, tmp_path, [
        (Message.assistant("one"), usage(100, 10)),
        (Message.assistant("two"), usage(200, 20)),
    ])
    try:
        agent._initialize_chat_session()
        run(agent, "first")
        middle = agent._session_revision_id
        run(agent, "second")
        session_id = agent._session_id
        ids = [item["id"] for item in agent.round_history]
        store = agent._session_store
    finally:
        agent.close()

    fresh = SessionStore(store.path.parent, session_id)
    assert [item["id"] for item in fresh.round_history()] == ids
    # The checkpoint after the first round sees only that round.
    assert [item["id"] for item in fresh.round_history(middle)] == ids[:1]

    resumed = make_agent(monkeypatch, tmp_path, [])
    events = EventHub()
    resumed.events = events
    try:
        assert resumed.load_session(session_id)
        assert [item["id"] for item in resumed.round_history] == ids
        history = [e.data for e in events.snapshot() if e.type == "round_history"]
        assert [item["id"] for item in history[-1]["rounds"]] == ids
    finally:
        resumed.close()


def test_history_stores_one_round_per_revision(monkeypatch, tmp_path):
    replies = [(Message.assistant(f"r{i}"), usage(100, 10)) for i in range(4)]
    agent = make_agent(monkeypatch, tmp_path, replies)
    try:
        agent._initialize_chat_session()
        for i in range(4):
            run(agent, f"prompt {i}")
        lines = agent._session_store.history_path.read_text(encoding="utf-8").splitlines()
    finally:
        agent.close()
    stored = [
        json.loads(line).get("stateChanged", {}).get("last_round")
        for line in lines if json.loads(line).get("type") == "revision"
    ]
    # Delta encoding keeps storage linear: each round is written once.
    assert [item["prompt"] for item in stored if item] == [f"prompt {i}" for i in range(4)]


def test_fork_carries_rounds_and_clear_resets_them(monkeypatch, tmp_path):
    agent = make_agent(monkeypatch, tmp_path, [
        (Message.assistant("one"), usage(100, 10)),
    ])
    try:
        agent._initialize_chat_session()
        run(agent, "first")
        rounds = agent.round_history
        data = agent._session_store.materialize()
        assert [item["id"] for item in data["round_history"]] == [rounds[0]["id"]]

        target = SessionStore(agent._session_store.path.parent, "forked")
        data.pop("revision_id")
        data.pop("code_revision_id")
        data["round_history_base"] = data.pop("round_history")
        data["last_round"] = None
        target.commit(data, parent_id=None, code_parent_id=None, changes=[], reason="fork")
        assert [item["id"] for item in target.round_history()] == [rounds[0]["id"]]

        agent._restart_session()
        assert agent.round_history == []
    finally:
        agent.close()


def test_shell_commands_are_not_rounds(monkeypatch, tmp_path):
    agent = make_agent(monkeypatch, tmp_path, [])
    try:
        agent._initialize_chat_session()
        agent._run_bash_command("echo hi")
        assert agent.round_history == []
    finally:
        agent.close()


def test_rounds_split_model_and_tool_time_with_latency(monkeypatch, tmp_path):
    agent = make_agent(monkeypatch, tmp_path, [])
    try:
        with agent._track_round():
            agent._note_model_call(2.0, ttft=0.5, output_tokens=300)
            agent._note_tool_time(1.25)
            agent._note_model_call(1.0, ttft=0.3, output_tokens=100)
            agent._note_model_call(0.4)  # not streamed: no latency figures
        record = agent.round_history[-1]
        assert record["model_calls"] == 3
        assert record["model_time"] == 3.4 and record["tool_time"] == 1.25
        assert record["ttft"] == 0.4
        assert record["tps"] == round(400 / 2.2, 1)

        with agent._track_round():
            agent._note_model_call(0.4)
        assert agent.round_history[-1]["ttft"] is None
        assert agent.round_history[-1]["tps"] is None
    finally:
        agent.close()


def test_rounds_time_real_model_calls_and_tools(monkeypatch, tmp_path):
    (tmp_path / "notes.txt").write_text("hello\n", encoding="utf-8")
    read = ToolCall(id="c1", name="read_file", arguments=json.dumps({"file": "notes.txt"}))
    agent = make_agent(monkeypatch, tmp_path, [
        (Message.assistant(None, tool_calls=[read]), usage(10, 5)),
        (Message.assistant("Done."), usage(20, 5)),
    ])
    try:
        agent._initialize_chat_session()
        run(agent, "read")
        record = agent.round_history[-1]
        assert record["model_calls"] == 2
        assert record["model_time"] >= 0 and record["tool_time"] >= 0
        assert record["model_time"] + record["tool_time"] <= record["duration"] + 0.01
        assert record["ttft"] is None  # blocking requests have no first-token time
    finally:
        agent.close()


def test_output_rate_meter_throttles_and_estimates_tokens_per_second():
    now = [100.0]
    published = []
    meter = OutputRateMeter(lambda chars: chars // 4, published.append, clock=lambda: now[0])
    meter.add(40)            # first delta starts the clock
    now[0] = 100.3
    meter.add(40)            # under MIN_SECONDS: too early to judge
    now[0] = 101.0
    meter.add(120)           # 200 chars -> 50 tokens over 1s
    now[0] = 101.2
    meter.add(40)            # throttled by INTERVAL
    meter.add(0)
    assert published == [50.0]
    now[0] = 102.0
    meter.add(40)            # 280 chars -> 70 tokens over 2s
    assert published == [50.0, 35.0]


def test_streaming_request_publishes_live_output_rate(monkeypatch, tmp_path):
    events = EventHub()
    agent = make_agent(monkeypatch, tmp_path, [], events=events)

    class Stream:
        def consume(self, *, on_content=None, on_output=None, **_):
            for _ in range(3):
                on_content("abcd")
                on_output(4)
            return CompletionResult(Message.assistant("abcdabcdabcd"), usage(10, 3), "stop")

        def close(self):
            pass

    agent.provider.open_stream = lambda request: Stream()
    agent.stream = True
    monkeypatch.setattr(OutputRateMeter, "MIN_SECONDS", 0.0)
    monkeypatch.setattr(OutputRateMeter, "INTERVAL", 0.0)
    agent.console.events = events
    try:
        agent._initialize_chat_session()
        run(agent, "hi")
        rates = [e.data["tps"] for e in events.snapshot() if e.type == "thinking_update" and "tps" in e.data]
        assert rates and all(rate > 0 for rate in rates)
    finally:
        agent.close()
