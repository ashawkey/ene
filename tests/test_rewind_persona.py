"""Rewind refuses a revision it cannot restore before changing anything."""

from pathlib import Path

import pytest

from test_rewind_ui import _build


@pytest.mark.parametrize("mode", ["1.", "2."])
def test_rewind_to_missing_persona_changes_nothing(tmp_path: Path, mode: str):
    agent, work, console = _build(tmp_path, [" 1.", mode])
    agent.personas = {}
    store = agent._session_store
    materialize = store.materialize
    store.materialize = lambda revision_id=None: {
        **materialize(revision_id), "persona": "retired"
    }
    head = store.head_id

    agent._cmd_rewind()

    assert "Unknown persona 'retired'" in console.text
    assert (work / "parser.py").read_text() == "def parse(text):\n    return float(text)\n"
    assert not (work / "util.py").exists()
    assert type(store)(store.path.parent, "session").head_id == head
    assert agent.round_id == 2
