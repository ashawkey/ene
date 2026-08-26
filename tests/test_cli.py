"""Tests for CLI model configuration."""

from rich import box

from ene.config import conf
from ene import cli
from ene.backend.sessions import _session_choice_labels
from ene.cli import Args, get_agent


def test_output_tables_use_a_borderless_header_rule():
    table = cli._output_table("Live sessions")

    assert table.box == box.SIMPLE_HEAD
    assert table.pad_edge is False
    assert table.title_justify == "left"


def test_session_choices_align_counts_and_truncate_preview_to_width():
    labels = _session_choice_labels(
        [
            ("20260101_120000", 9, 2, "short"),
            ("20260102_120000", 123, 45, "a long preview that should be truncated"),
        ],
        55,
    )

    assert labels[0] == "20260101_120000  msgs:  9  rounds: 2  short"
    assert labels[1] == "20260102_120000  msgs:123  rounds:45  a long preview t…"
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

    agent = get_agent(Args(model="test"))

    assert agent is not None
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

    agent = get_agent(Args(model="main"))

    assert agent is not None
    assert created[0]["model"] == "main-model"


def test_get_agent_rejects_invalid_recap_alias(monkeypatch):
    monkeypatch.setitem(conf, "openai", {"main": {"model": "main-model"}})
    monkeypatch.setitem(conf, "recap_model", "missing")

    assert get_agent(Args(model="main")) is None


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

    agent = get_agent(Args(model="main"))

    assert agent is not None
    assert created[0]["model"] == "main-model"


def test_get_agent_rejects_invalid_summary_alias(monkeypatch):
    monkeypatch.delitem(conf, "recap_model", raising=False)
    monkeypatch.setitem(conf, "openai", {"main": {"model": "main-model"}})
    monkeypatch.setitem(conf, "summary_model", "missing")

    assert get_agent(Args(model="main")) is None


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


def test_bare_invocation_starts_unnamed_new_session(monkeypatch):
    chats = []
    monkeypatch.setattr(cli, "cmd_chat", chats.append)

    assert cli.main([]) == 0

    assert chats[0].name == ""
    assert chats[0].resume is None


def test_bare_name_and_options_start_named_new_session(monkeypatch):
    chats = []
    monkeypatch.setattr(cli, "cmd_chat", chats.append)

    assert cli.main(["fresh", "--model", "test", "--no-stream"]) == 0

    assert chats[0].name == "fresh"
    assert chats[0].model == "test"
    assert chats[0].stream is False


def test_explicit_new_accepts_optional_name(monkeypatch):
    chats = []
    monkeypatch.setattr(cli, "cmd_chat", chats.append)

    assert cli.main(["new", "fresh", "--persona", "reviewer"]) == 0

    assert chats[0].name == "fresh"
    assert chats[0].persona == "reviewer"


def test_resume_accepts_optional_session(monkeypatch):
    chats = []
    monkeypatch.setattr(cli, "cmd_chat", chats.append)

    assert cli.main(["resume", "abc123", "--model", "test"]) == 0
    assert cli.main(["resume"]) == 0

    assert chats[0].resume == "abc123"
    assert chats[0].model == "test"
    assert chats[1].resume == ""


def test_r_alias_resumes_session(monkeypatch):
    chats = []
    monkeypatch.setattr(cli, "cmd_chat", chats.append)

    assert cli.main(["r", "abc123", "--model", "test"]) == 0

    assert chats[0].resume == "abc123"
    assert chats[0].model == "test"


def test_attach_uses_fuzzy_name_resolution(monkeypatch):
    record = {"runtime_id": "runtime", "name": "test"}
    resolved = []
    attached = []

    def resolve(identifier, *, fuzzy_name=False):
        resolved.append((identifier, fuzzy_name))
        return record

    monkeypatch.setattr("ene.live.resolve", resolve)
    monkeypatch.setattr(cli, "_attach_live", attached.append)

    cli.cmd_attach("te")

    assert resolved == [("te", True)]
    assert attached == [record]


def test_attach_switches_to_record_selected_before_detach(monkeypatch):
    current = {"runtime_id": "current"}
    other = {"runtime_id": "other"}
    attached = []
    startup_flags = []

    class Terminal:
        def __init__(self, record, *, switch_picker, new_session):
            attached.append(record)
            self.switch_record = None
            self.switch_picker = switch_picker
            self.new_session = new_session
            self.force_startup_panel = False

        def run(self):
            startup_flags.append(self.force_startup_panel)
            if len(attached) == 1:
                self.switch_record = self.switch_picker()
                return "switch", ""
            return "detach", ""

    monkeypatch.setattr("ene.live_terminal.LiveTerminal", Terminal)
    monkeypatch.setattr(cli, "_pick_live_record", lambda *args, **kwargs: other)

    cli._attach_live(current)

    assert attached == [current, other]
    assert startup_flags == [False, True]


def test_live_picker_has_cancel_option(monkeypatch):
    record = {
        "runtime_id": "other",
        "workspace": "/tmp/project",
        "busy": False,
    }
    choices_seen = []
    monkeypatch.setattr("ene.live.list_records", lambda: [record])
    monkeypatch.setattr(
        cli.AgentConsole,
        "select_terminal",
        lambda self, message, choices: choices_seen.extend(choices) or choices[-1],
    )

    current = {
        "name": "current",
        "runtime_id": "current-id",
        "workspace": "/tmp/current-project",
        "busy": True,
    }
    selected = cli._pick_live_record(
        cli.AgentConsole(), allow_cancel=True, current_record=current
    )

    assert selected is None
    assert choices_seen[0] == "other   ✓ done · needs review  /tmp/project"
    assert choices_seen[-1] == "Cancel  stay in current"


def test_live_picker_prioritizes_recent_done_sessions(monkeypatch):
    records = [
        {
            "runtime_id": "worknew",
            "workspace": "/tmp/working-new",
            "busy": True,
            "state_changed_at": 50,
        },
        {
            "runtime_id": "done-old",
            "workspace": "/tmp/done-old",
            "busy": False,
            "needs_attention": True,
            "state_changed_at": 20,
        },
        {
            "runtime_id": "done-new",
            "workspace": "/tmp/done-new",
            "busy": False,
            "needs_attention": True,
            "state_changed_at": 40,
        },
        {
            "runtime_id": "waiting",
            "workspace": "/tmp/waiting",
            "busy": False,
            "needs_attention": False,
            "state_changed_at": 60,
        },
    ]
    choices_seen = []
    monkeypatch.setattr("ene.live.list_records", lambda: records)
    monkeypatch.setattr(
        cli.AgentConsole,
        "select_terminal",
        lambda self, message, choices: choices_seen.extend(choices) or choices[0],
    )

    selected = cli._pick_live_record(cli.AgentConsole())

    assert selected["runtime_id"] == "done-new"
    assert [choice.split()[0] for choice in choices_seen] == [
        "done-new", "done-old", "waiting", "worknew",
    ]
    assert "✓ done · needs review" in choices_seen[0]
    assert "○ waiting" in choices_seen[2]
    assert "● working" in choices_seen[3]


def test_attach_loop_starts_named_new_session(monkeypatch):
    first = {
        "runtime_id": "first",
        "workspace": "/tmp/project",
        "options": {"model": "test", "resume": "old"},
    }
    second = {"runtime_id": "second"}
    actions = iter([("new", "fresh"), ("detach", "")])
    started = []

    class Terminal:
        def __init__(self, record, *, switch_picker, new_session):
            self.record = record
            self.new_record = None
            self.new_session = new_session

        def run(self):
            action = next(actions)
            if action[0] == "new":
                self.new_record = self.new_session(action[1])
            return action

    monkeypatch.setattr("ene.live_terminal.LiveTerminal", Terminal)
    monkeypatch.setattr(
        "ene.live.start_session",
        lambda **kwargs: started.append(kwargs) or second,
    )

    cli._attach_live(first)

    assert started == [{
        "name": "fresh",
        "workspace": "/tmp/project",
        "options": {"model": "test", "resume": None},
    }]


def test_attach_accepts_no_identifier(monkeypatch):
    attached = []
    monkeypatch.setattr(cli, "cmd_attach", attached.append)

    assert cli.main(["attach"]) == 0
    assert attached == [None]


def test_attach_short_alias(monkeypatch):
    attached = []
    monkeypatch.setattr(cli, "cmd_attach", attached.append)

    assert cli.main(["a", "agent"]) == 0
    assert attached == ["agent"]


def test_kill_short_alias_and_optional_identifier(monkeypatch):
    killed = []
    monkeypatch.setattr(cli, "cmd_kill", killed.append)

    assert cli.main(["k", "agent"]) == 0
    assert cli.main(["kill"]) == 0
    assert killed == ["agent", None]


def test_interactive_kill_selects_multiple_ready_sessions(monkeypatch):
    records = [
        {"runtime_id": "one-id", "name": "one", "status": "ready", "workspace": "/one"},
        {"runtime_id": "two-id", "name": "two", "status": "ready", "workspace": "/two"},
        {"runtime_id": "starting", "name": "wait", "status": "starting"},
    ]
    killed = []
    messages = []
    monkeypatch.setattr("ene.live.list_records", lambda: records)
    monkeypatch.setattr("ene.live.kill_session", killed.append)
    monkeypatch.setattr(
        cli.AgentConsole,
        "checkbox_terminal",
        lambda self, message, choices: choices,
    )
    monkeypatch.setattr(cli.AgentConsole, "system", lambda self, text: messages.append(text))

    cli.cmd_kill(None)

    assert killed == records[:2]
    assert messages == ["Killed session 'one'.", "Killed session 'two'."]


def test_interactive_kill_empty_selection_is_noop(monkeypatch):
    record = {"runtime_id": "one-id", "name": "one", "status": "ready"}
    monkeypatch.setattr("ene.live.list_records", lambda: [record])
    monkeypatch.setattr(cli.AgentConsole, "checkbox_terminal", lambda *args: [])
    monkeypatch.setattr(
        "ene.live.kill_session",
        lambda record: (_ for _ in ()).throw(AssertionError("must not kill")),
    )

    cli.cmd_kill(None)


def test_live_list_aliases(monkeypatch):
    listed = []
    monkeypatch.setattr(cli, "cmd_live_list", lambda: listed.append(True))

    for command in ("list", "ls", "l"):
        assert cli.main([command]) == 0

    assert listed == [True, True, True]


def test_models_lists_configured_models(monkeypatch):
    listed = []
    monkeypatch.setattr(cli, "cmd_models", lambda: listed.append(True))

    assert cli.main(["models"]) == 0
    assert listed == [True]


def test_live_picker_excludes_starting_sessions(monkeypatch):
    records = [{
        "runtime_id": "starting",
        "status": "starting",
        "workspace": "/tmp/project",
    }]
    monkeypatch.setattr("ene.live.list_records", lambda: records)
    messages = []
    monkeypatch.setattr(cli.AgentConsole, "system", lambda self, text: messages.append(text))

    assert cli._pick_live_record(cli.AgentConsole()) is None
    assert messages == ["No detached live sessions available."]


def test_live_list_shows_starting_state(monkeypatch):
    records = [{
        "name": "agent",
        "runtime_id": "123456789",
        "status": "starting",
        "attached": False,
        "busy": False,
        "model": "model",
        "conversation_id": "",
        "workspace": "/tmp/project",
    }]
    tables = []
    monkeypatch.setattr("ene.live.list_records", lambda: records)
    monkeypatch.setattr(cli.AgentConsole, "table", lambda self, table: tables.append(table))

    cli.cmd_live_list()

    assert str(tables[0].columns[2]._cells[0]) == "… starting"


def test_live_list_only_lists(monkeypatch):
    records = [{
        "name": "agent",
        "runtime_id": "123456789",
        "attached": False,
        "busy": False,
        "model": "model",
        "conversation_id": "conversation",
        "workspace": "/tmp/project",
    }]
    tables = []
    monkeypatch.setattr("ene.live.list_records", lambda: records)
    monkeypatch.setattr(cli.AgentConsole, "table", lambda self, table: tables.append(table))
    monkeypatch.setattr(
        cli.AgentConsole,
        "select_terminal",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not prompt")),
    )

    cli.cmd_live_list()

    assert len(tables) == 1
    assert str(tables[0].columns[2]._cells[0]) == "✓ done · needs review"


def test_lib_dispatches_remaining_arguments(monkeypatch):
    received = []
    monkeypatch.setattr("ene.library_cli.main", lambda argv: received.append(argv) or 7)

    assert cli.main(["lib", "list", "--local"]) == 7
    assert received == [["list", "--local"]]
