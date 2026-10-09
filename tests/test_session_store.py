import json
import shutil
from pathlib import Path

import pytest

from ene.messages import Message
from ene.session_store import MAX_BACKUP_FILE_SIZE, SessionStore
from ene.tools import ToolExecutor
from ene.ui import AgentConsole
from ene.utils.rewind import ChangeTracker


class _Console:
    def __init__(self):
        self.warnings = []

    def warn(self, message):
        self.warnings.append(message)


def _state(round_id: int, text: str) -> dict:
    return {
        "round_id": round_id,
        "messages": [Message.user(text)],
    }


@pytest.mark.parametrize("operation", ["write_file", "edit_file", "multi_edit"])
@pytest.mark.parametrize("link_kind", ["file", "directory", "external"])
def test_rewind_restores_symlink_targets_without_replacing_links(tmp_path, operation, link_kind):
    work = tmp_path / "work"
    work.mkdir()
    target_dir = tmp_path / "external" if link_kind == "external" else work / "real"
    target_dir.mkdir()
    target = target_dir / "value.txt"
    target.write_text("before")
    link = work / "link"
    link_target = "real" if link_kind == "directory" else (
        "../external/value.txt" if link_kind == "external" else "real/value.txt"
    )
    try:
        link.symlink_to(link_target, target_is_directory=link_kind == "directory")
    except OSError as exc:
        pytest.skip(f"Symlinks unavailable: {exc}")
    file = "link/value.txt" if link_kind == "directory" else "link"
    console = AgentConsole(render_terminal=False)
    store = SessionStore(tmp_path / "sessions", "session")
    tracker = ChangeTracker("session", work, console, store)
    executor = ToolExecutor(
        console=console, work_dir=str(work),
        change_tracker=tracker, get_round_id=lambda: 1,
    )
    edit = {"old_text": "before", "new_text": "after"}
    arguments = {"content": "after"} if operation == "write_file" else (
        {"edits": [edit]} if operation == "multi_edit" else edit
    )

    assert executor.execute(operation, {"file": file, **arguments})["success"]
    assert target.read_text() == "after"
    _, code, _ = store.commit(
        _state(1, "edit"), parent_id=None, code_parent_id=None,
        changes=tracker.pending_changes, reason="round",
    )
    tracker.mark_committed(code)

    # The persisted history must work after a restart too.
    tracker = ChangeTracker(
        "session", work, console, SessionStore(tmp_path / "sessions", "session"), code
    )
    plan = tracker.plan_checkout(None)
    assert not plan.dirty
    tracker.apply_plan(plan)
    assert target.read_text() == "before"
    assert link.is_symlink()
    assert link.readlink() == Path(link_target)

    tracker.checkout_code(code)
    assert target.read_text() == "after"
    assert link.is_symlink()
    assert link.readlink() == Path(link_target)


def test_rewind_write_through_dangling_symlink_removes_created_target(tmp_path):
    target = tmp_path / "new.txt"
    link = tmp_path / "link"
    try:
        link.symlink_to(target.name)
    except OSError as exc:
        pytest.skip(f"Symlinks unavailable: {exc}")
    console = AgentConsole(render_terminal=False)
    store = SessionStore(tmp_path / "sessions", "session")
    tracker = ChangeTracker("session", tmp_path, console, store)
    executor = ToolExecutor(
        console=console, work_dir=str(tmp_path),
        change_tracker=tracker, get_round_id=lambda: 1,
    )

    assert executor.execute("write_file", {"file": "link", "content": "new"})["success"]
    _, code, _ = store.commit(
        _state(1, "write"), parent_id=None, code_parent_id=None,
        changes=tracker.pending_changes, reason="round",
    )
    tracker.mark_committed(code)
    tracker.checkout_code(None)
    assert not target.exists()
    assert link.is_symlink()
    tracker.checkout_code(code)
    assert link.is_symlink()
    assert target.read_text() == "new"


def test_session_revisions_branch_without_losing_descendants(tmp_path: Path):
    store = SessionStore(tmp_path, "session")
    root, _, _ = store.commit(
        _state(1, "root"), parent_id=None, code_parent_id=None, changes=[], reason="round"
    )
    old_tip, _, _ = store.commit(
        _state(2, "old tip"), parent_id=root, code_parent_id=None, changes=[], reason="round"
    )

    store.checkout(root)
    new_tip, _, _ = store.commit(
        _state(2, "new tip"), parent_id=root, code_parent_id=None, changes=[], reason="round"
    )

    assert store.revisions[old_tip]["parentId"] == root
    assert store.revisions[new_tip]["parentId"] == root
    assert store.materialize(old_tip)["messages"][0].text == "old tip"
    assert store.materialize(new_tip)["messages"][0].text == "new tip"
    assert not (store.path / "snapshot.json").exists()


def test_revisions_are_delta_encoded_but_materialize_in_full(tmp_path: Path):
    """Appending a round must not rewrite the whole message list and state."""
    store = SessionStore(tmp_path, "session")
    system = Message.system("UNIQUE_PROMPT_MARKER " + "x" * 2000).to_wire()
    messages = []
    parent = None
    for round_id in range(1, 21):
        messages = messages + [Message.user(f"m{round_id}")]
        parent, _, _ = store.commit(
            {"messages": list(messages), "round_id": round_id, "system_prompt": system},
            parent_id=parent, code_parent_id=None, changes=[], reason="round",
        )

    history = (store.path / "history.jsonl").read_text(encoding="utf-8")
    # The unchanging system prompt is written once (with the root revision),
    # not once per round.
    assert history.count("UNIQUE_PROMPT_MARKER") == 1
    # Likewise each message ID is named once by the revision that added it,
    # instead of being repeated by every later revision.
    first_id = store.revisions[parent]["messageIds"][0]
    assert history.count(first_id) == 2  # the message record, and the root revision

    fresh = SessionStore(tmp_path, "session")
    data = fresh.materialize()
    assert data["messages"] == messages
    assert data["system_prompt"] == system and data["round_id"] == 20
    assert fresh.revisions == store.revisions


def test_commit_writes_picker_metadata_and_loads_it_without_history_parse(
    tmp_path: Path, monkeypatch
):
    store = SessionStore(tmp_path, "session")
    revision, _, _ = store.commit(
        {
            "messages": [
                Message.user("first"),
                Message.assistant("done"),
                Message.user("latest\nrequest"),
            ],
            "round_id": 2,
        },
        parent_id=None,
        code_parent_id=None,
        changes=[],
        reason="round",
        session_name="useful work",
    )

    monkeypatch.setattr(SessionStore, "_load", lambda self: pytest.fail("history was parsed"))
    summary = SessionStore.load_summary(tmp_path, "session")

    assert summary["head_revision_id"] == revision
    assert summary["session_name"] == "useful work"
    assert summary["message_count"] == 3
    assert summary["round_id"] == 2
    assert summary["last_user_message"] == "latest request"


def test_missing_or_stale_picker_metadata_is_rebuilt(tmp_path: Path):
    store = SessionStore(tmp_path, "session")
    revision, _, _ = store.commit(
        _state(1, "saved"), parent_id=None, code_parent_id=None, changes=[], reason="round"
    )
    store.meta_path.unlink()

    rebuilt = SessionStore.load_summary(tmp_path, "session")

    assert rebuilt["head_revision_id"] == revision
    assert store.meta_path.exists()
    assert json.loads(store.meta_path.read_text())["last_user_message"] == "saved"


def test_rename_updates_metadata_without_adding_history_revision(tmp_path: Path):
    store = SessionStore(tmp_path, "session")
    revision, _, _ = store.commit(
        _state(1, "saved"), parent_id=None, code_parent_id=None, changes=[], reason="round"
    )
    revision_count = len(store.revisions)

    store.rename("new name")

    assert len(store.revisions) == revision_count
    assert store.head_id == revision
    assert SessionStore.load_summary(tmp_path, "session")["session_name"] == "new name"


def test_renamed_metadata_survives_later_commits_and_checkout(tmp_path: Path):
    store = SessionStore(tmp_path, "session")
    root, _, _ = store.commit(
        _state(1, "root"), parent_id=None, code_parent_id=None, changes=[], reason="round"
    )
    store.rename("new name")
    tip, _, _ = store.commit(
        _state(2, "tip"), parent_id=root, code_parent_id=None, changes=[], reason="round"
    )
    store.checkout(root)

    summary = SessionStore.load_summary(tmp_path, "session")
    assert summary["head_revision_id"] == root
    assert summary["session_name"] == "new name"
    assert tip in store.revisions


def test_revision_state_is_snapshotted_not_aliased(tmp_path: Path):
    """A revision records the state it was written with, not a live reference.

    Callers pass their own mutable session state (token totals, the system
    prompt), which keeps changing after the commit returns.
    """
    store = SessionStore(tmp_path, "session")
    totals = {"total": 0}
    system = Message.system("persona A").to_wire()
    messages = [Message.user("m1")]

    first, _, _ = store.commit(
        {"messages": list(messages), "round_id": 1, "token_totals": totals,
         "system_prompt": system},
        parent_id=None, code_parent_id=None, changes=[], reason="initial",
    )
    totals["total"] = 12345
    system["content"] = "persona B"
    messages.append(Message.assistant("m2"))
    second, _, _ = store.commit(
        {"messages": list(messages), "round_id": 2, "token_totals": totals,
         "system_prompt": system},
        parent_id=first, code_parent_id=None, changes=[], reason="round",
    )

    reloaded = SessionStore(tmp_path, "session")
    assert reloaded.materialize(first)["token_totals"] == {"total": 0}
    assert reloaded.materialize(first)["system_prompt"]["content"] == "persona A"
    assert reloaded.materialize(second)["token_totals"] == {"total": 12345}
    assert reloaded.materialize(second)["system_prompt"]["content"] == "persona B"


def test_commit_picks_up_history_written_by_another_process(tmp_path: Path):
    """Saving reloads only when the file moved, but must never miss a change."""
    first_agent = SessionStore(tmp_path, "session")
    root, _, _ = first_agent.commit(
        _state(1, "root"), parent_id=None, code_parent_id=None, changes=[], reason="initial"
    )

    other_agent = SessionStore(tmp_path, "session")
    theirs, _, _ = other_agent.commit(
        _state(2, "theirs"), parent_id=root, code_parent_id=None, changes=[], reason="round"
    )

    mine, _, _ = first_agent.commit(
        _state(2, "mine"), parent_id=root, code_parent_id=None, changes=[], reason="round"
    )
    assert theirs in first_agent.revisions

    reloaded = SessionStore(tmp_path, "session")
    assert reloaded.materialize(theirs)["messages"][0].text == "theirs"
    assert reloaded.materialize(mine)["messages"][0].text == "mine"
    assert reloaded.head_id == mine


def test_delta_revisions_survive_branching_and_replacement(tmp_path: Path):
    """Rewind branches and compaction replace messages rather than appending."""
    store = SessionStore(tmp_path, "session")
    base = [Message.user("a")]
    root, _, _ = store.commit(
        {"messages": list(base), "round_id": 1, "goal": "g"},
        parent_id=None, code_parent_id=None, changes=[], reason="round",
    )
    appended = base + [Message.assistant("b")]
    tip, _, _ = store.commit(
        {"messages": list(appended), "round_id": 2, "goal": "g"},
        parent_id=root, code_parent_id=None, changes=[], reason="round",
    )
    # Divergent branch from the root.
    branched = base + [Message.assistant("other")]
    branch, _, _ = store.commit(
        {"messages": list(branched), "round_id": 2, "goal": "g2"},
        parent_id=root, code_parent_id=None, changes=[], reason="round",
    )
    # Compaction: messages replaced wholesale and a state key disappears.
    compacted = [Message.user("[summary]")]
    summary, _, _ = store.commit(
        {"messages": list(compacted), "round_id": 3},
        parent_id=tip, code_parent_id=None, changes=[], reason="round",
    )

    fresh = SessionStore(tmp_path, "session")
    assert fresh.materialize(root)["messages"] == base
    assert fresh.materialize(tip)["messages"] == appended
    assert fresh.materialize(branch)["messages"] == branched
    assert fresh.materialize(branch)["goal"] == "g2"
    assert fresh.materialize(summary)["messages"] == compacted
    assert "goal" not in fresh.materialize(summary)


def test_legacy_full_form_revisions_still_load(tmp_path: Path):
    """Histories written before delta encoding must remain readable."""
    store = SessionStore(tmp_path, "session")
    root, _, _ = store.commit(
        _state(1, "root"), parent_id=None, code_parent_id=None, changes=[], reason="round"
    )
    tip, _, _ = store.commit(
        _state(2, "tip"), parent_id=root, code_parent_id=None, changes=[], reason="round"
    )
    # Rewrite every revision record in the old full form (no delta keys).
    import json

    lines = []
    for line in (store.path / "history.jsonl").read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record.get("type") == "revision":
            revision = store.revisions[record["id"]]
            record = {
                key: value for key, value in record.items()
                if key not in ("baseCount", "addedIds", "stateChanged", "stateRemoved")
            }
            record["messageIds"] = revision["messageIds"]
            record["state"] = revision["state"]
        lines.append(json.dumps(record))
    (store.path / "history.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    fresh = SessionStore(tmp_path, "session")
    assert fresh.materialize(tip)["messages"][0].text == "tip"
    assert fresh.revisions == store.revisions
    # A new delta-encoded revision can still extend a legacy history.
    extended, _, _ = fresh.commit(
        _state(3, "next"), parent_id=tip, code_parent_id=None, changes=[], reason="round"
    )
    assert SessionStore(tmp_path, "session").materialize(extended)["round_id"] == 3


def test_session_history_ignores_only_a_torn_final_record(tmp_path: Path):
    store = SessionStore(tmp_path, "session")
    revision, _, _ = store.commit(
        _state(1, "saved"), parent_id=None, code_parent_id=None, changes=[], reason="round"
    )
    with store.history_path.open("ab") as f:
        f.write(b'{"type":"revision"')

    recovered = SessionStore(tmp_path, "session")
    assert recovered.head_id == revision

    with store.history_path.open("ab") as f:
        f.write(b"\n")
    with pytest.raises(ValueError, match="Corrupted JSONL"):
        SessionStore(tmp_path, "session")


def test_unterminated_final_record_is_not_read_before_the_next_write_drops_it(tmp_path: Path):
    store = SessionStore(tmp_path, "session")
    store.commit(
        _state(1, "saved"), parent_id=None, code_parent_id=None, changes=[], reason="round"
    )
    store.rename("kept")
    with store.history_path.open("ab") as f:
        f.write(b'{"type":"session_metadata","sessionName":"torn","createdAt":0}')

    assert SessionStore(tmp_path, "session").session_name == "kept"
    store.rename("kept")  # truncates the tail; nothing that was read is lost
    assert SessionStore(tmp_path, "session").session_name == "kept"


def test_conversation_rewind_to_unchanged_parent_moves_durable_head(tmp_path: Path):
    store = SessionStore(tmp_path, "session")
    first = Message.user("first")
    root, _, _ = store.commit(
        {"round_id": 1, "messages": [first]},
        parent_id=None, code_parent_id=None, changes=[], reason="round",
    )
    store.commit(
        {"round_id": 2, "messages": [first, Message.user("second")]},
        parent_id=root, code_parent_id=None, changes=[], reason="round",
    )

    revision, _, created = store.commit(
        {"round_id": 1, "messages": [first]},
        parent_id=root, code_parent_id=None, changes=[], reason="conversation-rewind",
    )

    assert (revision, created) == (root, False)
    reloaded = SessionStore(tmp_path, "session")
    assert reloaded.head_id == root
    assert reloaded.summary()["round_id"] == 1


def test_failed_append_does_not_mark_messages_as_stored(tmp_path: Path, monkeypatch):
    import ene.session_store as session_store

    store = SessionStore(tmp_path, "session")
    first = Message.user("first")
    root, _, _ = store.commit(
        {"messages": [first]}, parent_id=None, code_parent_id=None, changes=[], reason="round"
    )
    second = Message.user("second")

    def no_space(path, records):
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as patch:
        patch.setattr(session_store, "append_jsonl", no_space)
        with pytest.raises(OSError):
            store.commit(
                {"messages": [first, second]},
                parent_id=root, code_parent_id=None, changes=[], reason="round",
            )

    tip, _, _ = store.commit(
        {"messages": [first, second, Message.user("third")]},
        parent_id=root, code_parent_id=None, changes=[], reason="round",
    )
    restored = SessionStore(tmp_path, "session").materialize(tip)
    assert [message.text for message in restored["messages"]] == ["first", "second", "third"]


def test_code_revision_can_move_between_branches(tmp_path: Path):
    sessions = tmp_path / "sessions"
    work = tmp_path / "work"
    work.mkdir()
    target = work / "value.txt"
    target.write_text("zero")
    store = SessionStore(sessions, "session")
    tracker = ChangeTracker("session", work, _Console(), store)

    tracker.track_edit_result(1, str(target), "zero", "one")
    target.write_text("one")
    root, code_one, _ = store.commit(
        _state(1, "one"),
        parent_id=None,
        code_parent_id=None,
        changes=tracker.pending_changes,
        reason="round",
    )
    tracker.mark_committed(code_one)

    tracker.track_edit_result(2, str(target), "one", "two")
    target.write_text("two")
    old_tip, code_two, _ = store.commit(
        _state(2, "two"),
        parent_id=root,
        code_parent_id=code_one,
        changes=tracker.pending_changes,
        reason="round",
    )
    tracker.mark_committed(code_two)

    assert tracker.checkout_code(code_one) == 1
    assert target.read_text() == "one"
    store.checkout(root)

    tracker.track_edit_result(2, str(target), "one", "three")
    target.write_text("three")
    new_tip, code_three, _ = store.commit(
        _state(2, "three"),
        parent_id=root,
        code_parent_id=code_one,
        changes=tracker.pending_changes,
        reason="round",
    )
    tracker.mark_committed(code_three)

    assert tracker.checkout_code(code_two) == 2
    assert target.read_text() == "two"
    assert store.revisions[old_tip]["parentId"] == store.revisions[new_tip]["parentId"] == root


def test_removed_tree_uses_deduplicated_content_addressed_objects(tmp_path: Path):
    sessions = tmp_path / "sessions"
    work = tmp_path / "work"
    removed = work / "removed"
    removed.mkdir(parents=True)
    (removed / "a.bin").write_bytes(b"same bytes")
    (removed / "b.bin").write_bytes(b"same bytes")

    store = SessionStore(sessions, "session")
    tracker = ChangeTracker("session", work, _Console(), store)
    tracker.track_remove(1, str(removed))
    descriptor = tracker.pending_changes[0]["before"]
    shutil.rmtree(removed)

    root, code_revision, _ = store.commit(
        _state(1, "removed"),
        parent_id=None,
        code_parent_id=None,
        changes=tracker.pending_changes,
        reason="round",
    )
    tracker.mark_committed(code_revision)
    assert descriptor["kind"] == "tree"
    assert len([path for path in store.objects_path.rglob("*") if path.is_file()]) == 2

    tracker.checkout_code(None)
    assert (removed / "a.bin").read_bytes() == b"same bytes"
    assert (removed / "b.bin").read_bytes() == b"same bytes"
    assert store.revisions[root]["codeRevisionId"] == code_revision


def test_backup_limit_includes_exact_boundary(tmp_path):
    store = SessionStore(tmp_path / "sessions", "session")
    data = b"x" * MAX_BACKUP_FILE_SIZE
    descriptor = store.store_bytes(data)
    assert descriptor["kind"] == "file"
    assert store._object_path(descriptor["id"]).stat().st_size == MAX_BACKUP_FILE_SIZE
    assert store.store_bytes(data + b"x") == {
        "kind": "skipped", "size": MAX_BACKUP_FILE_SIZE + 1, "mode": 0o644,
    }
    assert len(list(store.objects_path.glob("*/*"))) == 1


@pytest.mark.parametrize("size", [8, 9])
def test_store_path_checks_size_before_reading(tmp_path, monkeypatch, size):
    monkeypatch.setattr("ene.session_store.MAX_BACKUP_FILE_SIZE", 8)
    store = SessionStore(tmp_path / "sessions", "session")
    target = tmp_path / "checkpoint.pt"
    target.write_bytes(b"x" * size)
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        if path == target and size > 8:
            pytest.fail("Oversized backup must not read file contents")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    descriptor = store.store_path(target)
    assert descriptor["kind"] == ("file" if size == 8 else "skipped")
    if size > 8:
        assert not store.objects_path.exists()


@pytest.mark.parametrize("operation", ["write_file", "edit_file", "multi_edit", "remove_file"])
def test_oversized_changes_warn_and_block_code_rewind(tmp_path, monkeypatch, operation):
    monkeypatch.setattr("ene.session_store.MAX_BACKUP_FILE_SIZE", 8)
    store, tracker, work = _session(tmp_path)
    target = work / "large.txt"
    target.write_text("0123456789")
    executor = ToolExecutor(
        console=AgentConsole(render_terminal=False), work_dir=str(work),
        change_tracker=tracker, get_round_id=lambda: 1,
    )
    edit = {"old_text": "0", "new_text": "X"}
    arguments = {"content": "small"} if operation == "write_file" else (
        {"edits": [edit]} if operation == "multi_edit" else (
            edit if operation == "edit_file" else {}
        )
    )
    assert executor.execute(operation, {"file": "large.txt", **arguments})["success"]
    assert "large.txt" in tracker.console.warnings[0]
    assert "10 MiB" in tracker.console.warnings[0]
    assert "rewind" in tracker.console.warnings[0]
    expected = target.read_bytes() if target.exists() else None
    _round(store, tracker, None, 1, "change large file")

    # The skipped marker and warning survive a restart; no large files are hashed.
    store = SessionStore(tmp_path / "sessions", "session")
    tracker = ChangeTracker("session", work, _Console(), store, store.materialize()["code_revision_id"])
    monkeypatch.setattr(store, "hash_path", lambda path: pytest.fail("Blocked rewind must not hash files"))
    plan = tracker.plan_checkout(None)
    assert plan and plan.skipped == ("large.txt",)
    assert store.candidates()
    with pytest.raises(RuntimeError, match="not backed up"):
        tracker.apply_plan(plan)
    assert (target.read_bytes() if target.exists() else None) == expected
    assert all(path.stat().st_size <= 8 for path in store.objects_path.glob("*/*"))


def test_new_oversized_file_cannot_be_replayed_or_deleted_by_rewind(tmp_path, monkeypatch):
    monkeypatch.setattr("ene.session_store.MAX_BACKUP_FILE_SIZE", 8)
    store, tracker, work = _session(tmp_path)
    target = work / "large.txt"
    tracker.track_write(1, str(target), "0123456789")
    target.write_text("0123456789")
    _round(store, tracker, None, 1, "create")
    code = tracker.code_revision_id
    assert tracker.plan_checkout(None).skipped == ("large.txt",)
    tracker.code_revision_id = None
    plan = tracker.plan_checkout(code)
    assert plan.skipped == ("large.txt",)
    with pytest.raises(RuntimeError, match="not backed up"):
        tracker.apply_plan(plan)
    assert target.read_text() == "0123456789"


def test_removed_tree_with_large_file_has_persistent_incomplete_backup(tmp_path, monkeypatch):
    monkeypatch.setattr("ene.session_store.MAX_BACKUP_FILE_SIZE", 8)
    store, tracker, work = _session(tmp_path)
    package = work / "package"
    nested = package / "checkpoints"
    nested.mkdir(parents=True)
    (package / "a.py").write_text("small")
    (nested / "model.pt").write_bytes(b"x" * 9)
    tracker.track_remove(1, str(package))
    descriptor = tracker.pending_changes[0]["before"]
    assert tracker.console.warnings == [
        "Not backed up for rewind: package/checkpoints/model.pt (exceeds the 10 MiB file limit). "
        "Code rewind across this change is unavailable."
    ]
    shutil.rmtree(package)
    _round(store, tracker, None, 1, "remove tree")
    plan = tracker.plan_checkout(None)
    assert plan.skipped == ("package/checkpoints/model.pt",)
    with pytest.raises(RuntimeError, match="not backed up"):
        tracker.apply_plan(plan)
    assert not package.exists()

    # Direct restore also refuses before creating any part of an incomplete tree.
    with pytest.raises(ValueError, match="not backed up"):
        store.restore_object(descriptor, package)
    assert not package.exists()
    assert sorted(path.stat().st_size for path in store.objects_path.glob("*/*"))[0] == 5


def test_blocked_checkout_does_not_partially_restore_other_files(tmp_path, monkeypatch):
    monkeypatch.setattr("ene.session_store.MAX_BACKUP_FILE_SIZE", 8)
    store, tracker, work = _session(tmp_path)
    large = work / "large.txt"
    large.write_text("0123456789")
    tracker.track_remove(1, str(large))
    large.unlink()
    _write(tracker, work, 1, "small.py", "small")
    _round(store, tracker, None, 1, "mixed changes")
    plan = tracker.plan_checkout(None)
    assert plan.steps[0][0]["path"] == "small.py"
    with pytest.raises(RuntimeError, match="not backed up"):
        tracker.apply_plan(plan)
    assert (work / "small.py").read_text() == "small"
    assert not large.exists()


def _session(tmp_path: Path) -> tuple[SessionStore, ChangeTracker, Path]:
    sessions = tmp_path / "sessions"
    work = tmp_path / "work"
    work.mkdir()
    store = SessionStore(sessions, "session")
    return store, ChangeTracker("session", work, _Console(), store), work


def _round(store, tracker, parent, round_id, text) -> str:
    revision_id, code_revision_id, _ = store.commit(
        _state(round_id, text),
        parent_id=parent,
        code_parent_id=tracker.code_revision_id,
        changes=tracker.pending_changes,
        reason="round",
    )
    tracker.mark_committed(code_revision_id)
    return revision_id


def _write(tracker, work, round_id, name, content) -> None:
    path = work / name
    original = path.read_text() if path.exists() else None
    if original is None:
        tracker.track_write(round_id, str(path), content)
    else:
        tracker.track_edit_result(round_id, str(path), original, content)
    path.write_text(content)


def test_candidates_are_pre_prompt_checkpoints_plus_current(tmp_path: Path):
    store, tracker, work = _session(tmp_path)
    initial, _, _ = store.commit(
        {"round_id": 0, "messages": []},
        parent_id=None,
        code_parent_id=None,
        changes=[],
        reason="initial",
    )
    first = _round(store, tracker, initial, 1, "start the parser")
    _write(tracker, work, 2, "parser.py", "a\nb\n")
    _write(tracker, work, 2, "util.py", "x\n")
    tip = _round(store, tracker, first, 2, "add the parser and a helper")

    before_first, before_second, current = store.candidates()
    assert before_first["id"] == initial and before_first["round_id"] == 0
    assert before_first["prompt"] == "start the parser"
    assert before_first["files"] == 0 and not before_first["current"]
    assert before_second["id"] == first and before_second["round_id"] == 1
    assert before_second["prompt"] == "add the parser and a helper"
    assert before_second["files"] == 2 and not before_second["current"]
    assert current["id"] == tip and current["round_id"] == 2
    assert current["prompt"] == "" and current["files"] == 0 and current["current"]


def test_candidates_skip_same_round_snapshots(tmp_path: Path):
    store, tracker, work = _session(tmp_path)
    initial, _, _ = store.commit(
        {"round_id": 0, "messages": []},
        parent_id=None,
        code_parent_id=None,
        changes=[],
        reason="initial",
    )
    first = _round(store, tracker, initial, 1, "one")
    messages = [
        Message.user("one"),
        Message.assistant("done"),
        Message.user("two"),
    ]
    _write(tracker, work, 2, "value.txt", "two\n")
    snapshot, code_revision, _ = store.commit(
        {"round_id": 2, "messages": messages},
        parent_id=first,
        code_parent_id=tracker.code_revision_id,
        changes=tracker.pending_changes,
        reason="pre-compaction",
    )
    tracker.mark_committed(code_revision)
    store.commit(
        {"round_id": 2, "messages": messages + [Message.assistant("done")]},
        parent_id=snapshot,
        code_parent_id=code_revision,
        changes=[],
        reason="round",
    )

    before_second = store.candidates()[1]
    assert before_second["id"] == first
    assert before_second["round_id"] == 1
    assert before_second["prompt"] == "two"
    assert before_second["files"] == 1


def test_plan_reports_no_file_changes_when_code_already_matches(tmp_path: Path):
    store, tracker, work = _session(tmp_path)
    _write(tracker, work, 1, "parser.py", "a\n")
    root = _round(store, tracker, None, 1, "one")
    # A revision that changes no files shares its parent's code revision.
    _round(store, tracker, root, 2, "two")

    plan = tracker.plan_checkout(store.materialize(root)["code_revision_id"])
    assert not plan
    assert plan.files == 0 and plan.steps == []
    assert tracker.apply_plan(plan) == 0
    assert (work / "parser.py").read_text() == "a\n"


def test_plan_classifies_and_counts_what_a_checkout_would_do(tmp_path: Path):
    store, tracker, work = _session(tmp_path)
    _write(tracker, work, 1, "keep.py", "one\ntwo\nthree\n")
    _write(tracker, work, 1, "gone.py", "old\n")
    root = _round(store, tracker, None, 1, "one")

    _write(tracker, work, 2, "keep.py", "one\nTWO\nthree\nfour\n")
    _write(tracker, work, 2, "added.py", "new\n")
    tracker.track_remove(2, str(work / "gone.py"))
    (work / "gone.py").unlink()
    tip = _round(store, tracker, root, 2, "two")

    plan = tracker.plan_checkout(store.materialize(root)["code_revision_id"])
    assert [(delta.op, delta.path) for delta in plan.deltas] == [
        ("delete", "added.py"),
        ("create", "gone.py"),
        ("modify", "keep.py"),
    ]
    assert (plan.added, plan.removed) == (2, 3)
    assert plan.dirty == ()

    tracker.apply_plan(plan)
    assert (work / "keep.py").read_text() == "one\ntwo\nthree\n"
    assert (work / "gone.py").read_text() == "old\n"
    assert not (work / "added.py").exists()
    assert tracker.code_revision_id == store.materialize(root)["code_revision_id"]

    # The plan is symmetric: replaying it forward restores the tip state.
    forward = tracker.plan_checkout(store.materialize(tip)["code_revision_id"])
    tracker.apply_plan(forward)
    assert (work / "keep.py").read_text() == "one\nTWO\nthree\nfour\n"
    assert (work / "added.py").read_text() == "new\n"
    assert not (work / "gone.py").exists()


def test_plan_flags_files_edited_outside_the_agent(tmp_path: Path):
    store, tracker, work = _session(tmp_path)
    _write(tracker, work, 1, "a.py", "one\n")
    _write(tracker, work, 1, "b.py", "one\n")
    root = _round(store, tracker, None, 1, "one")
    _write(tracker, work, 2, "a.py", "two\n")
    _write(tracker, work, 2, "b.py", "two\n")
    _round(store, tracker, root, 2, "two")

    assert tracker.plan_checkout(store.materialize(root)["code_revision_id"]).dirty == ()

    (work / "a.py").write_text("edited by hand\n")
    plan = tracker.plan_checkout(store.materialize(root)["code_revision_id"])
    assert plan.dirty == ("a.py",)


def test_line_endings_do_not_make_a_clean_tree_look_edited(tmp_path: Path):
    """Tools write text mode, so recorded bytes and disk bytes differ on Windows."""
    store, tracker, work = _session(tmp_path)
    path = work / "crlf.py"
    tracker.track_write(1, str(path), "one\ntwo\n")
    path.write_bytes(b"one\r\ntwo\r\n")
    root = _round(store, tracker, None, 1, "one")
    tracker.track_edit_result(2, str(path), "one\ntwo\n", "one\ntwo\nthree\n")
    path.write_bytes(b"one\r\ntwo\r\nthree\r\n")
    _round(store, tracker, root, 2, "two")

    plan = tracker.plan_checkout(store.materialize(root)["code_revision_id"])
    assert plan.dirty == ()
    assert (plan.added, plan.removed) == (0, 1)


def test_directory_trees_are_compared_against_disk_without_being_stored(tmp_path: Path):
    store, tracker, work = _session(tmp_path)
    package = work / "package"
    package.mkdir()
    (package / "a.py").write_text("one\n")
    (package / "b.py").write_text("two\n")
    root = _round(store, tracker, None, 1, "one")

    tracker.track_remove(2, str(package))
    shutil.rmtree(package)
    tip = _round(store, tracker, root, 2, "two")
    tracker.checkout_code(store.materialize(root)["code_revision_id"])

    forward = store.materialize(tip)["code_revision_id"]
    assert tracker.plan_checkout(forward).dirty == ()
    (package / "a.py").write_text("edited by hand\n")
    assert tracker.plan_checkout(forward).dirty == ("package",)


def test_checkout_requires_a_saved_session(tmp_path: Path):
    store, tracker, work = _session(tmp_path)
    _round(store, tracker, None, 1, "one")
    _write(tracker, work, 2, "a.py", "pending\n")

    with pytest.raises(RuntimeError, match="Save the session"):
        tracker.plan_checkout(None)


@pytest.mark.parametrize("operation", ["edit_file", "multi_edit"])
def test_rewind_restores_exact_original_bytes_after_edit(tmp_path, operation):
    store, tracker, work = _session(tmp_path)
    target = work / "crlf.txt"
    original = b"\xef\xbb\xbfa\r\nb  \r\nc\r\n"
    target.write_bytes(original)
    executor = ToolExecutor(
        console=AgentConsole(render_terminal=False), work_dir=str(work),
        change_tracker=tracker, get_round_id=lambda: 1,
    )
    edit = {"old_text": "b\n", "new_text": "B\n"}
    arguments = {"edits": [edit]} if operation == "multi_edit" else edit

    assert executor.execute(operation, {"file": "crlf.txt", **arguments})["success"]
    assert target.read_bytes() == b"\xef\xbb\xbfa\r\nB\r\nc\r\n"
    _round(store, tracker, None, 1, "edit")

    plan = tracker.plan_checkout(None)
    assert not plan.dirty
    tracker.apply_plan(plan)
    assert target.read_bytes() == original


def test_apply_plan_failure_names_already_restored_paths(tmp_path, monkeypatch):
    store, tracker, work = _session(tmp_path)
    _write(tracker, work, 1, "a.txt", "a\n")
    _write(tracker, work, 1, "b.txt", "b\n")
    _round(store, tracker, None, 1, "one")
    plan = tracker.plan_checkout(None)
    first_path = plan.steps[0][0]["path"]
    apply = tracker._apply

    def fail_second(record, *, forward):
        if record.path != first_path:
            raise OSError("disk full")
        return apply(record, forward=forward)

    monkeypatch.setattr(tracker, "_apply", fail_second)
    with pytest.raises(RuntimeError, match=f"disk full. Already restored: {first_path}$"):
        tracker.apply_plan(plan)
