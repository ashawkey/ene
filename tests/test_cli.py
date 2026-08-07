"""Tests for CLI model configuration."""

from ene.config import conf
from ene import cli
from ene.backend.sessions import _session_choice_labels
from ene.cli import Args, get_agent


def test_session_choices_align_counts_and_truncate_preview_to_width():
    labels = _session_choice_labels(
        [
            ("20260101_120000", 9, 2, "short"),
            ("20260102_120000", 123, 45, "a long preview that should be truncated"),
        ],
        55,
    )

    assert labels[0] == "20260101_120000  │  msgs:  9  rounds: 2  │  short"
    assert labels[1] == "20260102_120000  │  msgs:123  rounds:45  │  a long pre…"
    assert all(len(label) <= 55 for label in labels)


def test_get_agent_passes_token_limits(monkeypatch):
    monkeypatch.delitem(conf, "recap_model", raising=False)
    monkeypatch.delitem(conf, "summary_model", raising=False)
    created = []

    class FakeAgent:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setitem(conf, "openai", {
        "test": {
            "model": "test-model",
            "api_key": "key",
            "base_url": "url",
            "context_length": 200_000,
            "max_output_tokens": 16_000,
        }
    })
    monkeypatch.setattr("ene.cli.LLMAgent", FakeAgent)
    monkeypatch.setattr("ene.cli.discover_hub", lambda port: None)

    agent, hub_client = get_agent(Args(model="test"))

    assert agent is not None
    assert hub_client is None
    assert created[0]["provider_name"] == "openai"
    assert created[0]["context_length"] == 200_000
    assert created[0]["max_output_tokens"] == 16_000


def test_get_agent_accepts_configured_recap_alias(monkeypatch):
    monkeypatch.delitem(conf, "summary_model", raising=False)
    created = []

    class FakeAgent:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setitem(conf, "openai", {
        "main": {"model": "main-model"},
        "fast": {"model": "cheap-model"},
    })
    monkeypatch.setitem(conf, "recap_model", "fast")
    monkeypatch.setattr("ene.cli.LLMAgent", FakeAgent)
    monkeypatch.setattr("ene.cli.discover_hub", lambda port: None)

    agent, hub_client = get_agent(Args(model="main"))

    assert agent is not None
    assert hub_client is None
    assert created[0]["model"] == "main-model"


def test_get_agent_rejects_invalid_recap_alias(monkeypatch):
    monkeypatch.setitem(conf, "openai", {"main": {"model": "main-model"}})
    monkeypatch.setitem(conf, "recap_model", "missing")

    agent, hub_client = get_agent(Args(model="main"))

    assert agent is None
    assert hub_client is None


def test_get_agent_accepts_configured_summary_alias(monkeypatch):
    monkeypatch.delitem(conf, "recap_model", raising=False)
    created = []

    class FakeAgent:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setitem(conf, "openai", {
        "main": {"model": "main-model"},
        "fast": {"model": "cheap-model"},
    })
    monkeypatch.setitem(conf, "summary_model", "fast")
    monkeypatch.setattr("ene.cli.LLMAgent", FakeAgent)
    monkeypatch.setattr("ene.cli.discover_hub", lambda port: None)

    agent, hub_client = get_agent(Args(model="main"))

    assert agent is not None
    assert hub_client is None
    assert created[0]["model"] == "main-model"


def test_get_agent_rejects_invalid_summary_alias(monkeypatch):
    monkeypatch.delitem(conf, "recap_model", raising=False)
    monkeypatch.setitem(conf, "openai", {"main": {"model": "main-model"}})
    monkeypatch.setitem(conf, "summary_model", "missing")

    agent, hub_client = get_agent(Args(model="main"))

    assert agent is None
    assert hub_client is None


def test_clean_accepts_entry_names(monkeypatch):
    cleaned = []
    monkeypatch.setattr(
        cli,
        "cmd_clean",
        lambda names, *, history=False: cleaned.append((names, history)),
    )

    assert cli.main(["clean", "pdf-cache", "sessions"]) == 0

    assert cleaned == [(["pdf-cache", "sessions"], False)]


def test_clean_accepts_history_flag(monkeypatch):
    cleaned = []
    monkeypatch.setattr(
        cli,
        "cmd_clean",
        lambda names, *, history=False: cleaned.append((names, history)),
    )

    assert cli.main(["clean", "--history"]) == 0

    assert cleaned == [([], True)]


def test_clean_history_removes_sessions_and_disposable_entries(monkeypatch, tmp_path):
    sessions = tmp_path / ".ene" / "sessions"
    scratch = tmp_path / ".ene" / "scratch"
    sessions.mkdir(parents=True)
    scratch.mkdir()
    (sessions / "conversation.jsonl").write_text("history")
    (scratch / "notes.txt").write_text("temporary")
    monkeypatch.chdir(tmp_path)

    cli.cmd_clean(history=True)

    assert not sessions.exists()
    assert not scratch.exists()


def test_clean_history_combines_with_selected_entries(monkeypatch, tmp_path):
    sessions = tmp_path / ".ene" / "sessions"
    scratch = tmp_path / ".ene" / "scratch"
    cache = tmp_path / ".ene" / "cache"
    sessions.mkdir(parents=True)
    scratch.mkdir()
    cache.mkdir()
    monkeypatch.chdir(tmp_path)

    cli.cmd_clean(["cache"], history=True)

    assert not sessions.exists()
    assert not cache.exists()
    assert scratch.exists()


def test_implicit_chat_preserves_optional_resume(monkeypatch):
    chats = []
    monkeypatch.setattr(cli, "cmd_chat", chats.append)

    assert cli.main(["--model", "test", "--no-stream", "--resume"]) == 0

    assert chats[0].model == "test"
    assert chats[0].stream is False
    assert chats[0].resume == ""


def test_explicit_chat_resume_value(monkeypatch):
    chats = []
    monkeypatch.setattr(cli, "cmd_chat", chats.append)

    assert cli.main(["chat", "--resume", "abc123"]) == 0

    assert chats[0].resume == "abc123"


def test_lib_dispatches_remaining_arguments(monkeypatch):
    received = []
    monkeypatch.setattr("ene.library_cli.main", lambda argv: received.append(argv) or 7)

    assert cli.main(["lib", "list", "--local"]) == 7
    assert received == [["list", "--local"]]
