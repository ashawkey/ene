"""Read-only workspace browsing for the web hub.

Every path a browser supplies is relative to a live session's workspace and is
resolved (following symlinks) before use; anything that lands outside the
workspace root is refused. Nothing here writes to disk.
"""

from __future__ import annotations

import mimetypes
import os
import stat
import subprocess
from pathlib import Path


MAX_DIR_ENTRIES = 2000
MAX_TEXT_BYTES = 512 * 1024
MAX_RAW_BYTES = 16 * 1024 * 1024
BINARY_SNIFF_BYTES = 8192
GIT_TIMEOUT = 3.0
MAX_CHANGES = 500
# Raster formats only: an SVG is a document that can carry script, so it is
# shown as text instead of being served under the hub's origin.
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
    ".avif": "image/avif",
}


class WorkspaceError(Exception):
    """A browsing request that cannot be served; ``status`` is the HTTP code."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def resolve_in_workspace(root: str | Path, relative: str) -> tuple[Path, Path]:
    """Return ``(root, target)`` with *target* guaranteed inside *root*."""
    try:
        base = Path(root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise WorkspaceError("Session workspace is unavailable.") from exc
    if not base.is_dir():
        raise WorkspaceError("Session workspace is unavailable.")
    relative = (relative or "").strip()
    if "\x00" in relative or len(relative) > 4096:
        raise WorkspaceError("Invalid path.")
    # Absolute paths (as tools report them) are fine if they stay inside.
    relative = os.path.expanduser(relative) if relative.startswith("~") else relative
    try:
        target = (base / relative).resolve(strict=True)
    except FileNotFoundError as exc:
        raise WorkspaceError("Path not found.", 404) from exc
    except (OSError, RuntimeError) as exc:
        raise WorkspaceError("Path is not reachable.") from exc
    if target != base and base not in target.parents:
        raise WorkspaceError("Path is outside the workspace.", 403)
    return base, target


def _relative(base: Path, path: Path) -> str:
    rel = path.relative_to(base).as_posix()
    return "" if rel == "." else rel


def list_directory(root: str | Path, relative: str = "") -> dict:
    """List one directory level: folders first, then files, by name."""
    base, target = resolve_in_workspace(root, relative)
    if not target.is_dir():
        raise WorkspaceError("Not a directory.")
    entries: list[dict] = []
    truncated = False
    try:
        with os.scandir(target) as iterator:
            for item in iterator:
                if len(entries) >= MAX_DIR_ENTRIES:
                    truncated = True
                    break
                try:
                    info = item.stat(follow_symlinks=True)
                    kind = "dir" if stat.S_ISDIR(info.st_mode) else "file"
                    size = info.st_size if kind == "file" else 0
                    mtime = info.st_mtime
                except OSError:
                    # A dangling symlink still deserves a row, but no details.
                    kind, size, mtime = "file", 0, 0.0
                entries.append({
                    "name": item.name,
                    "type": kind,
                    "size": size,
                    "mtime": mtime,
                    "hidden": item.name.startswith("."),
                    "symlink": item.is_symlink(),
                })
    except OSError as exc:
        raise WorkspaceError("Directory is not readable.", 403) from exc
    entries.sort(key=lambda entry: (entry["type"] != "dir", entry["name"].lower()))
    return {
        "root": str(base),
        "path": _relative(base, target),
        "entries": entries,
        "truncated": truncated,
    }


def _is_binary(sample: bytes) -> bool:
    if b"\x00" in sample:
        return True
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A multi-byte character split at the sniff boundary is still text.
        return exc.start < len(sample) - 4
    return False


def read_file(root: str | Path, relative: str) -> dict:
    """Return a preview of one file: text (bounded), image, or binary marker."""
    base, target = resolve_in_workspace(root, relative)
    if not target.is_file():
        raise WorkspaceError("Not a file.")
    try:
        size = target.stat().st_size
        result = {
            "path": _relative(base, target),
            "name": target.name,
            "size": size,
            "mtime": target.stat().st_mtime,
        }
        suffix = target.suffix.lower()
        if suffix in IMAGE_TYPES:
            return {**result, "kind": "image", "mime": IMAGE_TYPES[suffix]}
        with target.open("rb") as handle:
            data = handle.read(MAX_TEXT_BYTES + 1)
    except OSError as exc:
        raise WorkspaceError("File is not readable.", 403) from exc
    if _is_binary(data[:BINARY_SNIFF_BYTES]):
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return {**result, "kind": "binary", "mime": mime}
    truncated = len(data) > MAX_TEXT_BYTES
    text = data[:MAX_TEXT_BYTES].decode("utf-8", errors="replace")
    return {**result, "kind": "text", "text": text, "truncated": truncated}


def raw_image(root: str | Path, relative: str) -> tuple[Path, str]:
    """Return a raster image's path and media type for direct serving."""
    _, target = resolve_in_workspace(root, relative)
    mime = IMAGE_TYPES.get(target.suffix.lower())
    if mime is None or not target.is_file():
        raise WorkspaceError("Only raster images can be served.", 415)
    try:
        if target.stat().st_size > MAX_RAW_BYTES:
            raise WorkspaceError("Image is too large to preview.", 413)
    except OSError as exc:
        raise WorkspaceError("File is not readable.", 403) from exc
    return target, mime


_GIT_CODES = {
    "M": "modified",
    "A": "added",
    "D": "deleted",
    "R": "renamed",
    "C": "copied",
    "U": "conflict",
    "?": "untracked",
}


def git_changes(root: str | Path) -> dict:
    """Summarize ``git status`` for the workspace; ``git: False`` outside a repo."""
    base, _ = resolve_in_workspace(root, "")
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=base, capture_output=True, text=True, timeout=GIT_TIMEOUT,
        )
        if top.returncode != 0:
            return {"git": False, "branch": "", "changes": []}
        repo = Path(top.stdout.strip()).resolve()
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=base, capture_output=True, text=True, timeout=GIT_TIMEOUT,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "."],
            cwd=base, capture_output=True, timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return {"git": False, "branch": "", "changes": []}
    changes: list[dict] = []
    records = status.stdout.decode("utf-8", errors="replace").split("\x00")
    index = 0
    while index < len(records) and len(changes) < MAX_CHANGES:
        record = records[index]
        index += 1
        if len(record) < 4:
            continue
        code = record[:2]
        path = record[3:]
        if "R" in code or "C" in code:
            index += 1  # skip the rename source
        letter = next((char for char in code if char not in " "), "M")
        absolute = repo / path
        try:
            relative = absolute.relative_to(base).as_posix()
        except ValueError:
            continue
        changes.append({
            "path": relative,
            "status": _GIT_CODES.get(letter, "modified"),
            "staged": code[0] not in " ?",
        })
    return {"git": True, "branch": branch, "changes": changes}
