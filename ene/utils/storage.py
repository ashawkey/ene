"""Inspect and clean project-local ene storage."""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .paths import ENE_GITIGNORE_NAME, get_ene_dir

# Entries a default clean keeps. Project instructions, skills, and personas are
# authored content; sessions are conversation history; batch results and
# orchestrator state are durable work products the agent may still be using.
# Losing any of them would be a real loss, unlike reclaimable caches, tool
# results, and scratch files.
PRESERVED_ENTRIES = frozenset(
    {"AGENTS.md", "skills", "personas", "sessions", "batch", "orchestrator"}
)


@dataclass(frozen=True)
class StorageEntry:
    name: str
    path: Path
    size: int
    is_dir: bool


def ene_storage_dir(cwd: str | Path | None = None) -> Path:
    return get_ene_dir(cwd)


def allocated_size(path: Path) -> int:
    """Return allocated bytes without following symbolic links."""
    stat = path.stat(follow_symlinks=False)
    size = getattr(stat, "st_blocks", 0) * 512 or stat.st_size
    if not path.is_dir() or path.is_symlink():
        return size

    with os.scandir(path) as children:
        return size + sum(allocated_size(Path(child.path)) for child in children)


def storage_entries(cwd: str | Path | None = None) -> list[StorageEntry]:
    root = ene_storage_dir(cwd)
    return [
        StorageEntry(
            name=path.name,
            path=path,
            size=allocated_size(path),
            is_dir=path.is_dir() and not path.is_symlink(),
        )
        for path in sorted(root.iterdir(), key=lambda path: path.name)
        if path.name != ENE_GITIGNORE_NAME
    ]


def cleanable_entries(cwd: str | Path | None = None) -> list[StorageEntry]:
    """Return entries removed by a default clean."""
    return [entry for entry in storage_entries(cwd) if entry.name not in PRESERVED_ENTRIES]


def clean_storage(
    cwd: str | Path | None = None,
    entries: list[StorageEntry] | None = None,
) -> int:
    """Delete selected entries, or all default-cleanable entries, and return their size."""
    if entries is None:
        entries = cleanable_entries(cwd)
    entries = [entry for entry in entries if entry.name != ENE_GITIGNORE_NAME]
    for entry in entries:
        if entry.is_dir:
            shutil.rmtree(entry.path)
        else:
            entry.path.unlink()
    return sum(entry.size for entry in entries)


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")
