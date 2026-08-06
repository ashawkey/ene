"""Tests for project-local agent storage cleanup."""

import subprocess

from ene.utils import get_ene_dir
from ene.utils.storage import clean_storage, cleanable_entries, storage_entries


def _write_entry(root, name: str, content: str = "data"):
    path = root / ".ene" / name
    path.mkdir(parents=True)
    (path / "data").write_text(content)
    return path


def _write_file_entry(root, name: str, content: str = "data"):
    path = root / ".ene" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def test_ene_dir_ignores_itself_and_all_contents(tmp_path):
    subprocess.run(
        ["git", "init", "--quiet"], cwd=tmp_path, check=True, capture_output=True
    )

    root = get_ene_dir(tmp_path)
    (root / "cache").mkdir()
    (root / "cache" / "data").write_text("data")

    assert (root / ".gitignore").read_text(encoding="utf-8") == "*\n"
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout == ""


def test_default_clean_removes_only_disposable_entries(tmp_path):
    instructions = _write_file_entry(tmp_path, "AGENTS.md")
    skills = _write_entry(tmp_path, "skills")
    personas = _write_entry(tmp_path, "personas")
    sessions = _write_entry(tmp_path, "sessions")
    batch = _write_entry(tmp_path, "batch")
    orchestrator = _write_entry(tmp_path, "orchestrator")
    tool_results = _write_entry(tmp_path, "tool-results")
    scratch = _write_entry(tmp_path, "scratch")
    custom_cache = _write_entry(tmp_path, "custom-cache")

    assert {entry.name for entry in cleanable_entries(tmp_path)} == {
        "tool-results",
        "scratch",
        "custom-cache",
    }

    removed = clean_storage(tmp_path)

    assert removed > 0
    assert instructions.exists()
    assert skills.exists()
    assert personas.exists()
    assert sessions.exists()
    assert batch.exists()
    assert orchestrator.exists()
    assert (tmp_path / ".ene" / ".gitignore").read_text(encoding="utf-8") == "*\n"
    assert not tool_results.exists()
    assert not scratch.exists()
    assert not custom_cache.exists()


def test_selected_clean_only_removes_selected_entries(tmp_path):
    skills = _write_entry(tmp_path, "skills")
    pdf_cache = _write_entry(tmp_path, "pdf-cache")
    entries = {entry.name: entry for entry in storage_entries(tmp_path)}

    clean_storage(entries=[entries["skills"]])

    assert not skills.exists()
    assert pdf_cache.exists()
