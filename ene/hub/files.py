"""Read-only workspace browsing for the web hub.

Every path a browser supplies is relative to a live session's workspace and is
resolved (following symlinks) before use; anything that lands outside the
workspace root is refused. Nothing here writes to disk.
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path


MAX_DIR_ENTRIES = 2000
MAX_TEXT_BYTES = 512 * 1024
MAX_RAW_BYTES = 16 * 1024 * 1024
BINARY_SNIFF_BYTES = 8192
GIT_TIMEOUT = 3.0
# `git status` walks the whole tree (untracked files included), which can take
# far longer than a ref lookup in a large repo or on a network filesystem.
GIT_STATUS_TIMEOUT = 15.0
MAX_CHANGES = 500
MAX_FIND_RESULTS = 50
MAX_QUERY_CHARS = 1000
# Full-text search limits: a broad query in a large tree stops early and is
# reported as truncated instead of stalling the hub.
MAX_SEARCH_MATCHES = 1000
MAX_SEARCH_FILES = 300
MAX_MATCHES_PER_FILE = 50
MAX_SEARCH_FILESIZE = "1M"
MAX_SEARCH_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_PREVIEW_CHARS = 240
SEARCH_TIMEOUT = 5.0
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


def git_branch(root: str | Path) -> dict:
    """Current branch (or short commit when detached); ``git: False`` outside a repo."""
    base, _ = resolve_in_workspace(root, "")
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=base, capture_output=True, text=True, timeout=GIT_TIMEOUT,
        )
        if head.returncode != 0:
            # An unborn branch has no HEAD commit yet but still has a name.
            unborn = subprocess.run(
                ["git", "symbolic-ref", "--short", "-q", "HEAD"],
                cwd=base, capture_output=True, text=True, timeout=GIT_TIMEOUT,
            )
            if unborn.returncode != 0:
                return {"git": False, "branch": "", "detached": False}
            return {"git": True, "branch": unborn.stdout.strip(), "detached": False}
        branch = head.stdout.strip()
        if branch != "HEAD":
            return {"git": True, "branch": branch, "detached": False}
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=base, capture_output=True, text=True, timeout=GIT_TIMEOUT,
        ).stdout.strip()
        return {"git": True, "branch": commit, "detached": True}
    except (OSError, subprocess.SubprocessError):
        return {"git": False, "branch": "", "detached": False}


def git_changes(root: str | Path) -> dict:
    """Summarize ``git status`` for the workspace; ``git: False`` outside a repo.

    Inside a repo the result keeps ``git: True`` even when ``git status`` fails
    or times out, carrying an ``error`` instead, so the UI still offers the
    changes view and can say why it is empty.
    """
    base, _ = resolve_in_workspace(root, "")
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=base, capture_output=True, text=True, timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return {"git": False, "branch": "", "changes": []}
    if top.returncode != 0:
        return {"git": False, "branch": "", "changes": []}
    repo = Path(top.stdout.strip()).resolve()
    branch = git_branch(base)["branch"]
    try:
        status = subprocess.run(
            ["git", "--no-optional-locks", "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "."],
            cwd=base, capture_output=True, timeout=GIT_STATUS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {
            "git": True, "branch": branch, "changes": [],
            "error": f"git status took longer than {GIT_STATUS_TIMEOUT:g}s.",
        }
    except (OSError, subprocess.SubprocessError):
        return {"git": True, "branch": branch, "changes": [], "error": "Could not run git status."}
    if status.returncode != 0:
        detail = status.stderr.decode("utf-8", errors="replace").strip().splitlines()
        message = detail[-1] if detail else "git status failed."
        return {"git": True, "branch": branch, "changes": [], "error": message[:300]}
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


def _git_text(data: bytes | None) -> tuple[str, bool, bool]:
    """``(text, binary, too_large)`` for one side of a diff."""
    if data is None:
        return "", False, False
    if len(data) > MAX_TEXT_BYTES:
        return "", False, True
    if _is_binary(data[:BINARY_SNIFF_BYTES]):
        return "", True, False
    return data.decode("utf-8", errors="replace"), False, False


def git_diff(root: str | Path, relative: str) -> dict:
    """Both sides of one file's working-tree change against ``HEAD``.

    The path may no longer exist (a deletion), so it is resolved without
    requiring existence and then confined to the workspace like any other.
    """
    base, _ = resolve_in_workspace(root, "")
    relative = (relative or "").strip()
    if not relative or "\x00" in relative or len(relative) > 4096:
        raise WorkspaceError("Invalid path.")
    try:
        target = (base / relative).resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise WorkspaceError("Path is not reachable.") from exc
    if base not in target.parents:
        raise WorkspaceError("Path is outside the workspace.", 403)
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=base, capture_output=True, text=True, timeout=GIT_TIMEOUT,
        )
        if top.returncode != 0:
            raise WorkspaceError("Not a Git repository.", 404)
        repo = Path(top.stdout.strip()).resolve()
        repo_path = target.relative_to(repo).as_posix()
        head = subprocess.run(
            ["git", "show", f"HEAD:{repo_path}"],
            cwd=repo, capture_output=True, timeout=GIT_TIMEOUT,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise WorkspaceError("Could not read the Git history.") from exc
    old_bytes = head.stdout if head.returncode == 0 else None
    try:
        new_bytes = target.read_bytes() if target.is_file() else None
    except OSError as exc:
        raise WorkspaceError("File is not readable.", 403) from exc
    if old_bytes is None and new_bytes is None:
        raise WorkspaceError("Path not found.", 404)
    old, old_binary, old_large = _git_text(old_bytes)
    new, new_binary, new_large = _git_text(new_bytes)
    status = (
        "added" if old_bytes is None
        else "deleted" if new_bytes is None
        else "modified" if old_bytes != new_bytes
        else "unchanged"
    )
    return {
        "path": target.relative_to(base).as_posix(),
        "status": status,
        "old": old,
        "new": new,
        "binary": old_binary or new_binary,
        "too_large": old_large or new_large,
    }


def _query(value: str) -> str:
    if not value or "\x00" in value or len(value) > MAX_QUERY_CHARS:
        raise WorkspaceError("Invalid search query.")
    return value


def find_files(completer, query: str, limit: int = MAX_FIND_RESULTS) -> dict:
    """Fuzzy-match file and folder paths across the workspace.

    *completer* is the hub's cached :class:`~ene.terminal.AtFileCompleter`
    for this workspace, so repeated keystrokes reuse one path index.
    """
    query = _query(query.strip())
    matches = completer.find(query.replace("\\", "/"), limit + 1)
    return {
        "results": [
            {"path": rel, "type": "dir" if is_dir else "file"}
            for rel, is_dir in matches[:limit]
        ],
        "truncated": len(matches) > limit,
    }


def _rg_text(value: dict | None) -> str:
    """Decode one ripgrep JSON text field (``text`` or base64 ``bytes``)."""
    if not value:
        return ""
    if "text" in value:
        return value["text"]
    try:
        return base64.b64decode(value.get("bytes", "")).decode("utf-8", errors="replace")
    except (ValueError, TypeError):
        return ""


def _preview(line: str, spans: list[tuple[int, int]]) -> tuple[str, list[list[int]], int]:
    """Clip a long line around its first match; return ``(text, ranges, offset)``."""
    line = line.rstrip("\r\n")
    offset = 0
    if len(line) > MAX_PREVIEW_CHARS:
        first = spans[0][0] if spans else 0
        offset = max(0, min(first - 40, len(line) - MAX_PREVIEW_CHARS))
        line = line[offset:offset + MAX_PREVIEW_CHARS]
    ranges = [
        [start - offset, min(end - offset, len(line))]
        for start, end in spans
        if start >= offset and start - offset < len(line)
    ]
    return line, ranges, offset


def _char_spans(raw: dict, line: str, submatches: list) -> list[tuple[int, int]]:
    """Convert ripgrep's byte offsets to character offsets in *line*."""
    if "text" not in raw:
        return []
    encoded = line.encode("utf-8")
    spans = []
    for item in submatches:
        try:
            start, end = int(item["start"]), int(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        a = len(encoded[:start].decode("utf-8", errors="replace"))
        b = a + len(encoded[start:end].decode("utf-8", errors="replace"))
        spans.append((a, b))
    return spans


class TextSearch:
    """Run ripgrep searches, at most one in flight per key (session).

    Starting a search kills the previous one for the same key, so a user
    typing quickly never stacks up ripgrep processes.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: dict[str, subprocess.Popen] = {}

    def cancel(self, key: str) -> None:
        with self._lock:
            process = self._running.pop(key, None)
        if process is not None:
            # Only signal: the owning request thread reads and closes the pipes.
            _signal(process)

    def search(
        self,
        key: str,
        root: str | Path,
        query: str,
        *,
        regex: bool = False,
        case_sensitive: bool = False,
        whole_word: bool = False,
        include: str = "",
        exclude: str = "",
    ) -> dict:
        base, _ = resolve_in_workspace(root, "")
        query = _query(query)
        rg = shutil.which("rg")
        if rg is None:
            raise WorkspaceError("Search requires ripgrep (rg) on the hub host.", 501)
        command = [
            rg, "--json", "--no-config", "--no-messages", "--hidden", "--no-require-git",
            "--glob", "!.git",
            "--max-count", str(MAX_MATCHES_PER_FILE),
            "--max-filesize", MAX_SEARCH_FILESIZE,
        ]
        command.append("--case-sensitive" if case_sensitive else "--ignore-case")
        if not regex:
            command.append("--fixed-strings")
        if whole_word:
            command.append("--word-regexp")
        for pattern in _globs(include):
            command += ["--glob", pattern]
        for pattern in _globs(exclude):
            command += ["--glob", f"!{pattern.lstrip('!')}"]
        command += ["--regexp", query, "--", "."]

        self.cancel(key)
        try:
            process = subprocess.Popen(
                command, cwd=base, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise WorkspaceError("Could not start the search.") from exc
        with self._lock:
            self._running[key] = process
        try:
            return self._collect(process, base)
        finally:
            with self._lock:
                if self._running.get(key) is process:
                    del self._running[key]
            _kill(process)

    def _collect(self, process: subprocess.Popen, base: Path) -> dict:
        deadline = time.monotonic() + SEARCH_TIMEOUT
        # The deadline is enforced from a timer: a read can block while rg
        # walks a large tree without printing anything.
        timer = threading.Timer(SEARCH_TIMEOUT, _signal, (process,))
        timer.daemon = True
        timer.start()
        files: list[dict] = []
        by_path: dict[str, dict] = {}
        match_count = 0
        read = 0
        reason: str | None = None
        assert process.stdout is not None
        try:
            for raw_line in process.stdout:
                read += len(raw_line)
                if read > MAX_SEARCH_OUTPUT_BYTES:
                    reason = "limit"
                    break
                try:
                    event = json.loads(raw_line)
                except ValueError:
                    continue
                if event.get("type") != "match":
                    continue
                data = event.get("data") or {}
                path = _rg_text(data.get("path"))
                path = path.replace("\\", "/")
                if path.startswith("./"):
                    path = path[2:]
                entry = by_path.get(path)
                if entry is None:
                    if len(files) >= MAX_SEARCH_FILES:
                        reason = "limit"
                        break
                    entry = {"path": path, "matches": [], "capped": False}
                    by_path[path] = entry
                    files.append(entry)
                lines = data.get("lines") or {}
                text = _rg_text(lines)
                spans = _char_spans(lines, text, data.get("submatches") or [])
                preview, ranges, offset = _preview(text, spans)
                entry["matches"].append({
                    "line": data.get("line_number") or 0,
                    "text": preview,
                    "ranges": ranges,
                    "clipped": offset > 0 or len(text.rstrip("\r\n")) > offset + len(preview),
                })
                # rg stops reading a file at --max-count matching lines.
                entry["capped"] = len(entry["matches"]) >= MAX_MATCHES_PER_FILE
                match_count += 1
                if match_count >= MAX_SEARCH_MATCHES:
                    reason = "limit"
                    break
        finally:
            timer.cancel()
        if reason is None and time.monotonic() >= deadline:
            reason = "timeout"
        if reason is None:
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            # Exit 1 means "no matches"; 2 is an error (e.g. a bad regex) but
            # rg also uses it for unreadable files alongside real results.
            if process.returncode == 2 and not files:
                detail = (process.stderr.read(2000) if process.stderr else b"").decode(
                    "utf-8", errors="replace"
                ).strip()
                if detail:
                    raise WorkspaceError(detail.splitlines()[-1][:300])
            elif process.returncode not in (0, 1, 2):
                # Killed by the next search for this session.
                reason = "cancelled"
        return {
            "root": str(base),
            "files": files,
            "match_count": match_count,
            "file_count": len(files),
            "truncated": reason is not None,
            "reason": reason,
            "per_file_limit": MAX_MATCHES_PER_FILE,
        }


def _globs(value: str) -> list[str]:
    if "\x00" in value or len(value) > MAX_QUERY_CHARS:
        raise WorkspaceError("Invalid file pattern.")
    return [part.strip() for part in value.split(",") if part.strip()]


def _signal(process: subprocess.Popen) -> None:
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def _kill(process: subprocess.Popen) -> None:
    _signal(process)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    for stream in (process.stdout, process.stderr):
        try:
            if stream is not None:
                stream.close()
        except OSError:
            pass
