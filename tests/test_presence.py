"""Tests for mutual awareness between agents sharing a workspace."""

import json
import os
import subprocess
import sys

from ene.utils.presence import PRESENCE_DIR_NAME, WorkspacePresence
from ene.utils import get_ene_dir


def _presence_dir(tmp_path):
    return get_ene_dir(tmp_path) / PRESENCE_DIR_NAME


def _exited_pid() -> int:
    """Return the PID of a process that has certainly exited and been reaped."""
    process = subprocess.Popen([sys.executable, "-c", ""])
    process.wait()
    return process.pid


def test_agents_in_one_workspace_see_each_other(tmp_path):
    first = WorkspacePresence(tmp_path, model="m1", persona="coder")
    second = WorkspacePresence(tmp_path, model="m2", persona="reviewer")

    assert first.refresh() == []

    second_peers = second.refresh()
    assert [peer.agent_id for peer in second_peers] == [first.agent_id]
    assert second_peers[0].model == "m1"
    assert second_peers[0].persona == "coder"
    assert second_peers[0].pid == os.getpid()

    assert [peer.agent_id for peer in first.refresh()] == [second.agent_id]

    second.close()

    assert first.refresh() == []


def test_construction_does_not_touch_the_workspace(tmp_path):
    WorkspacePresence(tmp_path)

    assert not (tmp_path / ".ene").exists()


def test_refresh_keeps_exactly_one_record_per_agent(tmp_path):
    presence = WorkspacePresence(tmp_path)

    presence.refresh()
    presence.refresh()
    presence.refresh()

    records = list(_presence_dir(tmp_path).glob("*.json"))
    assert [path.name for path in records] == [f"{presence.agent_id}.json"]


def test_peers_reads_without_publishing_own_record(tmp_path):
    reader = WorkspacePresence(tmp_path)
    other = WorkspacePresence(tmp_path, model="other")
    other.refresh()

    assert [peer.model for peer in reader.peers()] == ["other"]
    assert other.refresh() == []
    records = {path.name for path in _presence_dir(tmp_path).glob("*.json")}
    assert records == {f"{other.agent_id}.json"}


def test_refresh_restores_a_record_removed_underneath_it(tmp_path):
    presence = WorkspacePresence(tmp_path)
    observer = WorkspacePresence(tmp_path)
    presence.refresh()

    # e.g. `ene clean` wiping .ene/ while the agent keeps running.
    (_presence_dir(tmp_path) / f"{presence.agent_id}.json").unlink()
    presence.refresh()

    assert [peer.agent_id for peer in observer.peers()] == [presence.agent_id]


def test_update_republishes_changed_fields(tmp_path):
    presence = WorkspacePresence(tmp_path, model="old", session="")
    observer = WorkspacePresence(tmp_path)
    presence.refresh()

    presence.update(model="new", session="named")
    presence.refresh()

    peer = observer.refresh()[0]
    assert (peer.model, peer.session) == ("new", "named")


def test_dead_agent_record_is_reaped(tmp_path):
    directory = _presence_dir(tmp_path)
    directory.mkdir(parents=True, exist_ok=True)
    stale = directory / "deadagent.json"
    stale.write_text(
        json.dumps({
            "agent_id": "deadagent",
            "pid": _exited_pid(),
            "model": "m",
            "persona": "coder",
            "session": "",
            "started_at": 1.0,
        }),
        encoding="utf-8",
    )

    assert WorkspacePresence(tmp_path).refresh() == []
    assert not stale.exists()


def test_unreadable_record_is_reaped_without_raising(tmp_path):
    directory = _presence_dir(tmp_path)
    directory.mkdir(parents=True, exist_ok=True)
    broken = directory / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    missing_pid = directory / "nopid.json"
    missing_pid.write_text(json.dumps({"agent_id": "nopid"}), encoding="utf-8")

    assert WorkspacePresence(tmp_path).refresh() == []
    assert not broken.exists()
    assert not missing_pid.exists()


def test_peers_are_ordered_by_start_time(tmp_path):
    observer = WorkspacePresence(tmp_path)
    older = WorkspacePresence(tmp_path, model="older")
    newer = WorkspacePresence(tmp_path, model="newer")
    older.started_at = 100.0
    newer.started_at = 200.0
    newer.refresh()
    older.refresh()

    assert [peer.model for peer in observer.refresh()] == ["older", "newer"]


def test_close_is_idempotent_and_survives_a_missing_record(tmp_path):
    presence = WorkspacePresence(tmp_path)
    presence.refresh()
    (_presence_dir(tmp_path) / f"{presence.agent_id}.json").unlink()

    presence.close()
    presence.close()
