"""Uploads into a session workspace and zip downloads out of it.

Uploads stream into a hidden part file beside their destination and are
renamed into place only once every byte has arrived, so a cancelled, dropped,
or oversized upload never leaves a partial file under the real name. Message
attachments are uploads into ``.ene/uploads/<conversation>/<token>/`` (ignored
by Git), removed when unsent at session end and swept after a week.

Zip downloads are planned up front (so limits are HTTP errors, not truncated
archives) and then streamed without a temporary file.
"""

from __future__ import annotations

import io
import os
import re
import secrets
import shutil
import stat
import time
import zipfile
from pathlib import Path
from typing import Iterator

from ..utils.paths import get_ene_dir
from .files import WorkspaceError, resolve_in_workspace


MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_UPLOAD_SEGMENTS = 32        # folder depth of one uploaded path
MAX_CHECK_NAMES = 1000
MAX_ZIP_FILES = 1000
MAX_ZIP_BYTES = 2 * 1024 ** 3   # uncompressed
MAX_ZIP_PATHS = 200
ZIP_CHUNK = 1024 * 1024
PART_PREFIX = ".ene-upload-"
PART_SUFFIX = ".part"
# Part files left by a killed hub; swept from a folder on its next upload.
STALE_PART_SECONDS = 24 * 60 * 60
ATTACHMENT_DIR = "uploads"
ATTACHMENT_TTL = 7 * 24 * 60 * 60


def _check_segment(segment: str) -> str:
    if (
        not segment
        or len(segment.encode("utf-8", "surrogatepass")) > 255
        or segment in (".", "..")
        or any(char in "/\\" or ord(char) < 32 or ord(char) == 127 for char in segment)
    ):
        raise WorkspaceError("Invalid file name.")
    return segment


def upload_segments(name: str) -> list[str]:
    """Split a ``/``-separated upload path (a dropped folder's file) into checked names."""
    parts = (name or "").split("/")
    if len(parts) > MAX_UPLOAD_SEGMENTS:
        raise WorkspaceError("The folder is nested too deeply.")
    return [_check_segment(part) for part in parts]


def _confined(base: Path, path: Path) -> bool:
    return path == base or base in path.parents


class Upload:
    """One streaming upload: ``write`` chunks, then ``commit`` or ``abort``."""

    def __init__(
        self, final: Path, overwrite: bool, relative: str, scratch: Path | None = None
    ):
        self.final = final
        self.overwrite = overwrite
        self.relative = relative
        # A folder created just for this upload, removed if it is abandoned.
        self.scratch = scratch
        self.part = final.parent / f"{PART_PREFIX}{secrets.token_hex(8)}{PART_SUFFIX}"
        self.size = 0
        self._fd: int | None = os.open(
            self.part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o666
        )

    def write(self, data: bytes) -> None:
        assert self._fd is not None
        view = memoryview(data)
        while view:
            written = os.write(self._fd, view)
            view = view[written:]
        self.size += len(data)

    def _close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def commit(self) -> dict:
        try:
            self._close()
            if os.path.lexists(self.final):
                if os.path.isdir(self.final) and not os.path.islink(self.final):
                    raise WorkspaceError("A folder with that name already exists.", 409)
                if not self.overwrite:
                    raise WorkspaceError("A file with that name already exists.", 409)
                # A replaced file keeps its permissions (an executable stays one).
                try:
                    os.chmod(self.part, stat.S_IMODE(os.stat(self.final).st_mode))
                except OSError:
                    pass
            os.replace(self.part, self.final)
        except WorkspaceError:
            self.abort()
            raise
        except OSError as exc:
            self.abort()
            raise WorkspaceError("Could not save the file.", 403) from exc
        return {"path": self.relative, "size": self.size}

    def abort(self) -> None:
        """Close and delete the part file; safe to call more than once."""
        try:
            self._close()
        except OSError:
            pass
        try:
            os.unlink(self.part)
        except OSError:
            pass
        if self.scratch is not None:
            shutil.rmtree(self.scratch, ignore_errors=True)
            _remove_if_empty(self.scratch.parent)


def _remove_if_empty(folder: Path) -> None:
    try:
        os.rmdir(folder)
    except OSError:
        pass


def _sweep_parts(folder: Path) -> None:
    cutoff = time.time() - STALE_PART_SECONDS
    try:
        with os.scandir(folder) as iterator:
            for item in iterator:
                if not (item.name.startswith(PART_PREFIX) and item.name.endswith(PART_SUFFIX)):
                    continue
                try:
                    if item.is_file(follow_symlinks=False) and item.stat(follow_symlinks=False).st_mtime < cutoff:
                        os.unlink(item.path)
                except OSError:
                    pass
    except OSError:
        pass


def prepare_upload(root: str | Path, directory: str, name: str, overwrite: bool) -> Upload:
    """Open an upload of *name* (which may include subfolders) into workspace *directory*."""
    segments = upload_segments(name)
    base, target = resolve_in_workspace(root, directory)
    if not target.is_dir():
        raise WorkspaceError("Not a directory.")
    folder = target
    try:
        for segment in segments[:-1]:
            folder = folder / segment
            if not folder.is_dir():
                folder.mkdir()
            # A symlinked folder must not lead the upload out of the workspace.
            folder = folder.resolve(strict=True)
            if not _confined(base, folder):
                raise WorkspaceError("Path is outside the workspace.", 403)
    except FileExistsError as exc:
        raise WorkspaceError("A file blocks the folder path.", 409) from exc
    except OSError as exc:
        raise WorkspaceError("Cannot create a folder here.", 403) from exc
    final = folder / segments[-1]
    if os.path.isdir(final) and not os.path.islink(final):
        raise WorkspaceError("A folder with that name already exists.", 409)
    if os.path.lexists(final) and not overwrite:
        raise WorkspaceError("A file with that name already exists.", 409)
    _sweep_parts(folder)
    try:
        return Upload(final, overwrite, final.relative_to(base).as_posix())
    except OSError as exc:
        raise WorkspaceError("Cannot write to this folder.", 403) from exc


def existing_uploads(root: str | Path, directory: str, names: list[str]) -> list[str]:
    """The subset of *names* that would replace something in *directory*."""
    if len(names) > MAX_CHECK_NAMES:
        raise WorkspaceError("Too many files.")
    _, target = resolve_in_workspace(root, directory)
    if not target.is_dir():
        raise WorkspaceError("Not a directory.")
    found = []
    for name in names:
        segments = upload_segments(name)
        if os.path.lexists(target.joinpath(*segments)):
            found.append(name)
    return found


# -- message attachments -------------------------------------------------------

def _attachment_root(root: str | Path) -> tuple[Path, Path]:
    base, _ = resolve_in_workspace(root, "")
    try:
        uploads = get_ene_dir(base) / ATTACHMENT_DIR
        uploads.mkdir(exist_ok=True)
    except OSError as exc:
        raise WorkspaceError("Cannot write to the workspace.", 403) from exc
    return base, uploads


def attachment_name(name: str) -> str:
    """A safe single file name for an attachment, keeping what it can of *name*.

    Whitespace becomes ``_`` too, so an ``@path`` mention stays one token.
    """
    cleaned = re.sub(r'[\x00-\x1f\x7f/\\\s]', "_", (name or "").strip()).strip(". ")
    while len(cleaned.encode("utf-8", "surrogatepass")) > 200:
        cleaned = cleaned[1:]
    return cleaned or "attachment"


def _sweep_attachments(uploads: Path) -> None:
    cutoff = time.time() - ATTACHMENT_TTL
    try:
        buckets = list(os.scandir(uploads))
    except OSError:
        return
    for bucket in buckets:
        try:
            if not bucket.is_dir(follow_symlinks=False):
                continue
            entries = list(os.scandir(bucket.path))
            for entry in entries:
                if entry.is_dir(follow_symlinks=False) and entry.stat(follow_symlinks=False).st_mtime < cutoff:
                    shutil.rmtree(entry.path, ignore_errors=True)
            _remove_if_empty(Path(bucket.path))
        except OSError:
            continue


def prepare_attachment(root: str | Path, bucket: str, name: str) -> Upload:
    """Open an upload into a fresh ``.ene/uploads/<bucket>/<token>/`` folder."""
    base, uploads = _attachment_root(root)
    _sweep_attachments(uploads)
    bucket = re.sub(r"[^A-Za-z0-9_.-]", "_", bucket).strip(".")[:80] or "session"
    folder = uploads / bucket / secrets.token_hex(6)
    final = folder / attachment_name(name)
    try:
        folder.mkdir(parents=True)
        return Upload(final, False, final.relative_to(base).as_posix(), scratch=folder)
    except OSError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise WorkspaceError("Cannot write to the workspace.", 403) from exc


def delete_attachment(root: str | Path, relative: str) -> None:
    """Delete one attachment and its token folder; refuses anything else."""
    try:
        base, uploads = _attachment_root(root)
        uploads = uploads.resolve(strict=True)
    except OSError as exc:
        raise WorkspaceError("Session workspace is unavailable.") from exc
    if "\x00" in (relative or ""):
        raise WorkspaceError("Not an attachment.", 403)
    candidate = Path(os.path.normpath(base / relative))
    token_dir = candidate.parent
    if token_dir.parent.parent != uploads or candidate.name in ("", ".", ".."):
        raise WorkspaceError("Not an attachment.", 403)
    if os.path.islink(token_dir) or os.path.islink(token_dir.parent) or not token_dir.is_dir():
        return
    shutil.rmtree(token_dir, ignore_errors=True)
    _remove_if_empty(token_dir.parent)


# -- zip downloads ---------------------------------------------------------------

class _Sink(io.RawIOBase):
    """An unseekable sink that hands written bytes to the stream."""

    def __init__(self) -> None:
        self._chunks: list[bytes] = []

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:
        self._chunks.append(bytes(data))
        return len(data)

    def drain(self) -> bytes:
        data = b"".join(self._chunks)
        self._chunks.clear()
        return data


def zip_plan(root: str | Path, paths: list[str]) -> tuple[str, list[tuple[Path, str]], int]:
    """Return the archive name, ``(file, name in archive)`` pairs, and total size.

    Folders are walked without following symlinks; symlinked files that land
    outside the workspace are skipped. Archive names are relative to the
    selection's common folder.
    """
    if not paths:
        raise WorkspaceError("Nothing to download.")
    if len(paths) > MAX_ZIP_PATHS:
        raise WorkspaceError(f"Select at most {MAX_ZIP_PATHS} items.")
    base, _ = resolve_in_workspace(root, "")
    targets = list(dict.fromkeys(resolve_in_workspace(root, path)[1] for path in paths))
    # Entries keep their own names at the archive's top level.
    common = Path(os.path.commonpath([str(target.parent) for target in targets]))
    files: list[tuple[Path, str]] = []
    seen: set[str] = set()
    total = 0

    def add(path: Path) -> None:
        nonlocal total
        try:
            real = path.resolve(strict=True)
            info = real.stat()
        except (OSError, RuntimeError):
            return
        if not stat.S_ISREG(info.st_mode) or not _confined(base, real):
            return
        try:
            arcname = path.relative_to(common).as_posix()
        except ValueError:
            arcname = path.name
        if arcname in seen:
            return
        seen.add(arcname)
        total += info.st_size
        if len(files) >= MAX_ZIP_FILES:
            raise WorkspaceError(f"Too many files to zip (the limit is {MAX_ZIP_FILES}).", 413)
        if total > MAX_ZIP_BYTES:
            raise WorkspaceError("The selection is too large to zip (the limit is 2 GB).", 413)
        files.append((path, arcname))

    for target in targets:
        if target.is_dir():
            for folder, dirs, names in os.walk(target):
                dirs.sort()
                for item in sorted(names):
                    add(Path(folder) / item)
        else:
            add(target)
    if not files:
        raise WorkspaceError("No files to download.")
    if len(targets) == 1:
        name = targets[0].name
    else:
        name = common.name if _confined(base, common) else base.name
    return f"{name or 'download'}.zip", files, total


def zip_stream(files: list[tuple[Path, str]]) -> Iterator[bytes]:
    """Yield a zip archive of *files* as it is built."""
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, arcname in files:
            try:
                source = open(path, "rb")
                info = zipfile.ZipInfo.from_file(path, arcname, strict_timestamps=False)
            except OSError:
                continue  # vanished or unreadable since planning
            info.compress_type = zipfile.ZIP_DEFLATED
            # zipfile sizes Zip64 headers from ``file_size``, as planned.
            with source, archive.open(info, "w") as entry:
                while True:
                    chunk = source.read(ZIP_CHUNK)
                    if not chunk:
                        break
                    entry.write(chunk)
                    data = sink.drain()
                    if data:
                        yield data
            data = sink.drain()
            if data:
                yield data
    data = sink.drain()
    if data:
        yield data
