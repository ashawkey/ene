"""Tests for what /rewind shows before it changes anything."""

import io
import types
from pathlib import Path

from rich.console import Console

from ene.backend.sessions import SessionMixin
from ene.session_store import SessionStore
from ene.utils.rewind import ChangeTracker


class _Console:
    """Capture rendered output and answer prompts from a script."""

    def __init__(self, answers: list[str] | None = None):
        self.buffer = io.StringIO()
        self.rich = Console(file=self.buffer, width=200, no_color=True, legacy_windows=False)
        self.answers = list(answers or [])
        self.prompts: list[list[str]] = []

    def print(self, *args, **kwargs):
        self.rich.print(*args, **kwargs)

    table = print

    def _log(self, message, **kwargs):
        self.rich.print(message, markup=False)

    system = warn = error = user_input = response = thinking_message = tool = _log

    def tool_result(self, message, success=True):
        self._log(f"{'ok' if success else 'fail'}: {message}")

    def reset_timeline(self):
        self._log("[timeline reset]")

    def select(self, message, choices, **kwargs):
        self.prompts.append(choices)
        wanted = self.answers.pop(0)
        return next(choice for choice in choices if choice.startswith(wanted))

    @property
    def text(self) -> str:
        return self.buffer.getvalue()


class _Agent(SessionMixin):
    """SessionMixin with just the collaborators /rewind touches."""

    def __init__(self, store, tracker, console):
        self.console = console
        self._session_id = "session"
        self._session_store = store
        self._session_revision_id = store.head_id
        self.changes = tracker
        self.context = types.SimpleNamespace(messages=[])
        self.round_id = 0
        self.replayed = 0
        self.show_thinking = True

    def save_session(self, name=None, *, reason="autosave"):
        revision_id, code_revision_id, _ = self._session_store.commit(
            {"round_id": self.round_id, "messages": list(self.context.messages)},
            parent_id=self._session_revision_id,
            code_parent_id=self.changes.code_revision_id,
            changes=self.changes.pending_changes,
            reason=reason,
        )
        self._session_revision_id = revision_id
        self.changes.mark_committed(code_revision_id)

    def _restore_session_data(self, data):
        self.context.messages = list(data["messages"])
        self.round_id = data.get("round_id", 0)

    def _replay_context(self):
        self.replayed += 1


def _build(tmp_path: Path, answers: list[str]) -> tuple[_Agent, Path, _Console]:
    """Two rounds: one adds files, the next edits one and deletes another."""
    work = tmp_path / "work"
    work.mkdir()
    store = SessionStore(tmp_path / "sessions", "session")
    console = _Console(answers)
    tracker = ChangeTracker("session", work, console, store)
    agent = _Agent(store, tracker, console)
    agent.save_session(reason="initial")

    agent.round_id = 1
    agent.context.messages = [{"role": "user", "content": "set up the parser module"}]
    for name, body in (("parser.py", "def parse():\n    return 1\n"), ("util.py", "X = 1\n")):
        tracker.track_write(1, str(work / name), body)
        (work / name).write_text(body)
    agent.save_session(reason="round")

    agent.round_id = 2
    agent.context.messages += [
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "make the parser handle floats and drop util"},
    ]
    new = "def parse(text):\n    return float(text)\n"
    tracker.track_edit_result(2, str(work / "parser.py"), (work / "parser.py").read_text(), new)
    (work / "parser.py").write_text(new)
    tracker.track_remove(2, str(work / "util.py"))
    (work / "util.py").unlink()
    agent.save_session(reason="round")
    return agent, work, console


def test_picker_and_preview_describe_the_conversation_and_the_files(tmp_path: Path):
    agent, work, console = _build(tmp_path, [" 1.", "1."])
    agent._cmd_rewind()

    picker, modes = console.prompts
    assert "make the parser handle floats and drop util" in picker[0]
    assert "2 files" in picker[0]
    assert "set up the parser module" in picker[1]
    # The preview names every file the checkout touches, and how it touches it.
    assert "modify  parser.py" in console.text
    assert "create  util.py" in console.text
    assert "1 round(s) will be dropped" in console.text
    assert "round 2  make the parser handle floats and drop util" in console.text
    # The mode labels answer "will this revert my files?" before anything happens.
    assert "3 → 1 messages" in modes[0]
    assert "2 file(s) reverted" in modes[0]
    assert "files untouched" in modes[1]
    assert modes[3] == "4. Cancel"
    assert all("diff" not in mode.lower() for mode in modes)

    assert (work / "parser.py").read_text() == "def parse():\n    return 1\n"
    assert (work / "util.py").read_text() == "X = 1\n"
    assert agent.round_id == 1 and agent.replayed == 1
    assert agent._rewind_draft == "make the parser handle floats and drop util"


def test_picker_options_are_the_listing_and_name_the_revision_type(tmp_path: Path):
    """No table precedes the picker, so every option must stand on its own."""
    agent, _, console = _build(tmp_path, [" 1.", "4."])
    agent.context.messages.append({"role": "user", "content": "one more thought"})
    agent.save_session(reason="pre-compaction")
    agent._cmd_rewind()

    picker = console.prompts[0]
    assert "round 1" in picker[0] and "2 files" in picker[0]
    assert "make the parser handle floats and drop util" in picker[0]
    assert "round 0" in picker[1] and "set up the parser module" in picker[1]
    assert "(session start)" in picker[1]
    assert len(picker) == 2
    assert all("[current]" not in option for option in picker)
    assert all("(current state)" not in option for option in picker)
    # The options replace the table rather than repeating it.
    assert "Session revisions" not in console.text


def test_conversation_only_rewind_leaves_the_files_alone(tmp_path: Path):
    agent, work, console = _build(tmp_path, [" 1.", "2."])
    agent._cmd_rewind()

    assert (work / "parser.py").read_text() == "def parse(text):\n    return float(text)\n"
    assert not (work / "util.py").exists()
    assert len(agent.context.messages) == 1


def test_external_file_changes_disable_code_rewind(tmp_path: Path):
    agent, work, console = _build(tmp_path, [" 1.", "1."])
    (work / "parser.py").write_text("hand edited\n")
    agent._cmd_rewind()

    assert "Code cannot be reverted" in console.text
    assert "changed on disk" in console.text
    assert "parser.py" in console.text
    assert console.prompts[1] == [
        "1. Conversation only — 3 → 1 messages, files untouched",
        "2. Cancel",
    ]
    assert (work / "parser.py").read_text() == "hand edited\n"
    assert not (work / "util.py").exists()
    assert len(agent.context.messages) == 1


def test_replay_omits_tool_activity(tmp_path: Path):
    agent, _, console = _build(tmp_path, [])
    agent.context.messages = [
        {"role": "user", "content": "check the file"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "1", "function": {"name": "read_file", "arguments": '{"file": "a.txt"}'}},
        ]},
        {
            "role": "tool",
            "tool_call_id": "1",
            "content": "1  an error message",
            "display_content": "1 line read",
        },
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "2", "function": {"name": "edit_file", "arguments": '{"file": "a.txt", "old_text": "x", "new_text": "y"}'}},
        ]},
        {"role": "tool", "tool_call_id": "2", "content": "Error: File not found: a.txt"},
    ]
    SessionMixin._replay_context(agent)

    assert "check the file" in console.text
    assert "read_file" not in console.text
    assert "edit_file" not in console.text
    assert "1 line read" not in console.text
    assert "File not found" not in console.text
    assert "4 messages hidden" in console.text


def test_replay_shows_all_saved_conversation_turns(tmp_path: Path):
    agent, _, console = _build(tmp_path, [])
    agent.context.messages = []
    for iteration in range(1, 13):
        agent.context.messages.extend([
            {"role": "user", "content": f"prompt {iteration}"},
            {"role": "assistant", "content": f"answer {iteration}"},
        ])

    SessionMixin._replay_context(agent)

    assert "prompt 1\n" in console.text
    assert "answer 2\n" in console.text
    assert "prompt 3\n" in console.text
    assert "answer 12\n" in console.text
    assert "messages hidden" not in console.text


def test_replay_omits_reasoning_only_assistant_messages(tmp_path: Path):
    agent, _, console = _build(tmp_path, [])
    agent.context.messages = [
        {"role": "user", "content": "work it out"},
        {
            "role": "assistant",
            "content": None,
            "reasoning_content": "the retained final reasoning",
            "provider_state": {"openai-codex": {}},
        },
    ]

    SessionMixin._replay_context(agent)

    assert "work it out" in console.text
    assert "the retained final reasoning" not in console.text


def test_replay_shows_only_final_text_response_for_a_user_turn(tmp_path: Path):
    agent, _, console = _build(tmp_path, [])
    agent.context.messages = [
        {"role": "user", "content": "finish this"},
        {"role": "assistant", "content": "intermediate", "tool_calls": [
            {"id": "1", "function": {"name": "read_file", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "1", "content": "tool output"},
        {"role": "assistant", "content": "final answer"},
    ]

    SessionMixin._replay_context(agent)

    assert "finish this" in console.text
    assert "final answer" in console.text
    assert "intermediate" not in console.text
    assert "tool output" not in console.text
    assert "2 messages hidden" in console.text


def test_no_file_changes_only_offers_conversation_rewind(tmp_path: Path):
    agent, _, console = _build(tmp_path, [" 1.", "1."])
    agent.round_id = 3
    agent.context.messages.append({"role": "user", "content": "just talking"})
    agent.save_session(reason="round")
    agent._cmd_rewind()

    assert "no files will change" in console.text
    assert console.prompts[1] == [
        "1. Conversation only — 4 → 3 messages, files untouched",
        "2. Cancel",
    ]
    assert len(agent.context.messages) == 3
