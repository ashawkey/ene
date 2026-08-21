"""Git-backed personal skill library used by the ``ene lib`` command."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shlex
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from filelock import FileLock

from ene.personas import read_persona, valid_persona_name
from ene.skills import read_skill, valid_skill_name
from ene.utils import get_ene_dir


logger = logging.getLogger(__name__)


class LibraryError(RuntimeError):
    """A user-facing resource library error."""


def _resource_config(
    kind: str,
) -> tuple[str, str, Callable[..., object], Callable[[str], bool]]:
    if kind == "skill":
        return "skills", "SKILL.md", read_skill, valid_skill_name
    if kind == "persona":
        return "personas", "PERSONA.md", read_persona, valid_persona_name
    raise LibraryError(f"invalid library resource kind: {kind!r}")


def _git(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    command = ["git", *args]
    display_command = command.copy()
    if args[:2] == ("clone", "--quiet"):
        display_command[-2] = "<repository>"
    command_text = shlex.join(display_command)
    location = f" (cwd: {cwd})" if cwd is not None else ""
    logger.info("Running %s%s", command_text, location)
    started = time.monotonic()
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise LibraryError("Git is not installed or is not available on PATH") from exc
    logger.info(
        "Finished %s with exit code %d in %.2fs",
        command_text,
        result.returncode,
        time.monotonic() - started,
    )
    if result.stdout.strip():
        logger.info("Git stdout: %s", result.stdout.strip())
    if result.stderr.strip():
        logger.info("Git stderr: %s", result.stderr.strip())
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise LibraryError(f"git {' '.join(args)} failed: {detail}")
    return result


def _validate_repo(repo: str) -> str:
    if not isinstance(repo, str) or not repo.strip():
        raise LibraryError(
            "ene_lib is not configured; add a GitHub repository URL to ~/.ene.yaml, "
            "for example: ene_lib: git@github.com:user/ene-skills.git"
        )
    return repo.strip()


def _sync_checkout(root: Path) -> None:
    """Reset the managed checkout to the remote main branch."""
    logger.info("Refreshing cached checkout")
    _git("reset", "--hard", cwd=root, check=False)
    _git("clean", "-fdx", cwd=root)
    _git("fetch", "--quiet", "--prune", "origin", cwd=root)

    # The pruning fetch already resolved origin/main, so this stays local.
    main = _git(
        "rev-parse", "--verify", "--quiet", "refs/remotes/origin/main",
        cwd=root,
        check=False,
    )
    if main.returncode == 0:
        _git("checkout", "--quiet", "-B", "main", "origin/main", cwd=root)
    elif not _git("ls-remote", "origin", cwd=root).stdout.strip():
        # An empty remote has no branch yet; the first upload creates main.
        _git("update-ref", "-d", "refs/heads/main", cwd=root)
        _git("symbolic-ref", "HEAD", "refs/heads/main", cwd=root)
        _git("reset", cwd=root, check=False)
        _git("clean", "-fdx", cwd=root)
    else:
        raise LibraryError("repository has no main branch")


_active_checkouts: dict[str, Path] = {}
_pending_pushes: set[str] = set()


def _push_main(repo: str, checkout: Path) -> None:
    """Publish main, coalescing pushes into one round trip inside a batch."""
    repo = _validate_repo(repo)
    if repo in _active_checkouts:
        _pending_pushes.add(repo)
        return
    _git("push", "--quiet", "origin", "main", cwd=checkout)


@contextmanager
def batch_session(repo: str) -> Iterator[None]:
    """Share one locked, refreshed checkout across a batch of operations.

    Commits made by the batch stay local until it ends, then publish in a
    single push. The push also runs when an operation fails, so a partly
    completed batch keeps the same progress it would have pushed per item.
    """
    repo = _validate_repo(repo)
    if repo in _active_checkouts:
        yield
        return
    with _checkout(repo) as root:
        _active_checkouts[repo] = root
        try:
            yield
        except BaseException:
            _publish_batch(repo, root, reraising=True)
            raise
        else:
            _publish_batch(repo, root, reraising=False)
        finally:
            _active_checkouts.pop(repo, None)
            _pending_pushes.discard(repo)


def _publish_batch(repo: str, root: Path, reraising: bool) -> None:
    """Push a batch's accumulated commits, if it made any."""
    if repo not in _pending_pushes:
        return
    try:
        _git("push", "--quiet", "origin", "main", cwd=root)
    except LibraryError:
        # A failing operation is the more useful error to surface.
        if not reraising:
            raise
        logger.warning("Could not push batched commits for %s", repo)


@contextmanager
def _checkout(repo: str) -> Iterator[Path]:
    """Lock and refresh a persistent checkout fixed to the main branch."""
    repo = _validate_repo(repo)
    active = _active_checkouts.get(repo)
    if active is not None:
        logger.info("Reusing refreshed checkout: %s", active)
        yield active
        return
    library_root = Path.home() / ".ene" / "library"
    key = hashlib.sha256(repo.encode()).hexdigest()[:16]
    root = library_root / key
    library_root.mkdir(parents=True, exist_ok=True)

    lock_path = library_root / f"{key}.lock"
    logger.info("Library cache: %s", root)
    logger.info("Waiting for cache lock: %s", lock_path)
    with FileLock(str(lock_path)):
        logger.info("Acquired cache lock")
        creating = not root.exists()
        try:
            if creating:
                logger.info("Cloning library repository")
                _git("clone", "--quiet", repo, str(root))
            elif not (root / ".git").is_dir():
                raise LibraryError(f"invalid library cache: {root}")
            _sync_checkout(root)
        except LibraryError as exc:
            if creating and root.exists():
                shutil.rmtree(root)
            if str(exc).startswith((
                "Git is not installed",
                "invalid library cache",
                "repository has no main branch",
            )):
                raise
            raise LibraryError(
                "cannot access the ene_lib repository using the current Git "
                f"credentials: {exc}"
            ) from exc
        yield root


def _validate_tree(root: Path) -> None:
    if root.is_symlink():
        raise LibraryError(f"resource directory is a symlink: {root}")
    for current, dirs, files in os.walk(root, followlinks=False):
        base = Path(current)
        for name in dirs:
            path = base / name
            relative = path.relative_to(root)
            if name == ".git":
                raise LibraryError(f"resource contains Git metadata: {relative}")
            mode = path.stat(follow_symlinks=False).st_mode
            if stat.S_ISLNK(mode):
                raise LibraryError(f"resource contains a symlink: {relative}")
            if not stat.S_ISDIR(mode):
                raise LibraryError(f"resource contains an invalid directory: {relative}")
        for name in files:
            path = base / name
            relative = path.relative_to(root)
            if name == ".git":
                raise LibraryError(f"resource contains Git metadata: {relative}")
            mode = path.stat(follow_symlinks=False).st_mode
            if stat.S_ISLNK(mode):
                raise LibraryError(f"resource contains a symlink: {relative}")
            if not stat.S_ISREG(mode):
                raise LibraryError(f"resource contains a non-regular file: {relative}")


def _load_strict(resource_dir: Path, kind: str = "skill") -> dict:
    _, _, reader, _ = _resource_config(kind)
    try:
        _validate_tree(resource_dir)
        _sync_digest(resource_dir)
        if kind == "skill":
            return reader(resource_dir, strict=True)
        info = reader(resource_dir, source="library")
        return {"description": info.description, "persona": info}
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise LibraryError(f"invalid {kind} '{resource_dir.name}': {exc}") from exc


def _resources_root(checkout: Path, kind: str = "skill") -> Path:
    dirname, _, _, _ = _resource_config(kind)
    root = checkout / dirname
    if root.is_symlink():
        raise LibraryError(f"library {dirname} directory is a symlink")
    if root.exists() and not root.is_dir():
        raise LibraryError(f"library {dirname} path is not a directory")
    return root


def _skills_root(checkout: Path) -> Path:
    return _resources_root(checkout, "skill")


def _copy_tree(source: Path, dest: Path) -> None:
    """Copy regular files without following source symlinks.

    POSIX uses an fd-relative walk, which also resists symlink-swap races;
    platforms without ``dir_fd`` (e.g. Windows) fall back to a path-based
    walk that re-validates each file after opening it.
    """
    dest.mkdir()
    if os.name == "posix":
        _copy_tree_fd(source, dest)
    else:
        _copy_tree_paths(source, dest)


def _check_copy_entry(entry: os.DirEntry, mode: int) -> None:
    if entry.name == ".git":
        raise LibraryError(f"resource contains Git metadata: {entry.name}")
    if stat.S_ISLNK(mode):
        raise LibraryError(f"resource contains a symlink: {entry.name}")
    if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
        raise LibraryError(f"resource contains a non-regular file: {entry.name}")


def _copy_tree_fd(source: Path, dest: Path) -> None:
    def copy_dir(source_fd: int, target: Path) -> None:
        for entry in os.scandir(source_fd):
            relative = target / entry.name
            mode = entry.stat(follow_symlinks=False).st_mode
            _check_copy_entry(entry, mode)
            if stat.S_ISDIR(mode):
                relative.mkdir()
                child_fd = os.open(
                    entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=source_fd,
                )
                try:
                    copy_dir(child_fd, relative)
                finally:
                    os.close(child_fd)
            elif stat.S_ISREG(mode):
                source_file = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=source_fd)
                try:
                    opened_mode = os.fstat(source_file).st_mode
                    if not stat.S_ISREG(opened_mode):
                        raise LibraryError(f"resource contains a non-regular file: {entry.name}")
                    with os.fdopen(source_file, "rb", closefd=False) as src, relative.open("xb") as dst:
                        shutil.copyfileobj(src, dst)
                    relative.chmod(stat.S_IMODE(opened_mode))
                finally:
                    os.close(source_file)

    source_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        copy_dir(source_fd, dest)
    finally:
        os.close(source_fd)


def _copy_tree_paths(source: Path, dest: Path) -> None:
    def copy_dir(source_dir: Path, target: Path) -> None:
        for entry in os.scandir(source_dir):
            relative = target / entry.name
            mode = entry.stat(follow_symlinks=False).st_mode
            _check_copy_entry(entry, mode)
            if stat.S_ISDIR(mode):
                relative.mkdir()
                copy_dir(Path(entry.path), relative)
            elif stat.S_ISREG(mode):
                with open(entry.path, "rb") as src:
                    opened_mode = os.fstat(src.fileno()).st_mode
                    if not stat.S_ISREG(opened_mode):
                        raise LibraryError(f"resource contains a non-regular file: {entry.name}")
                    with relative.open("xb") as dst:
                        shutil.copyfileobj(src, dst)
                relative.chmod(stat.S_IMODE(opened_mode))

    copy_dir(source, dest)


@contextmanager
def _snapshot_resource(source: Path, kind: str = "skill") -> Iterator[Path]:
    """Copy and validate an immutable snapshot of a local resource."""
    with tempfile.TemporaryDirectory(prefix=f"ene-{kind}-") as temp_dir:
        snapshot = Path(temp_dir) / source.name
        try:
            _copy_tree(source, snapshot)
        except OSError as exc:
            raise LibraryError(f"cannot snapshot local {kind} '{source.name}': {exc}") from exc
        _load_strict(snapshot, kind)
        yield snapshot


def list_local_resources(
    kind: str = "skill",
    work_dir: str | Path | None = None,
) -> tuple[dict[str, dict], list[dict]]:
    """Return resources installed in the current project's ``.ene`` directory."""
    dirname, _, reader, _ = _resource_config(kind)
    base = Path(work_dir) if work_dir is not None else Path.cwd()
    root = get_ene_dir(base) / dirname
    logger.info("Scanning local %s in %s", dirname, root)
    resources: dict[str, dict] = {}
    errors: list[dict] = []
    if not root.is_dir():
        return resources, errors

    for item in sorted(root.iterdir(), key=lambda path: path.name):
        if not item.is_dir():
            continue
        try:
            if kind == "skill":
                resources[item.name] = reader(item)
            else:
                info = reader(item, source="project")
                resources[item.name] = {"description": info.description, "persona": info}
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            errors.append({"name": item.name, "reason": str(exc)})
    return resources, errors


def list_local_skills(work_dir: str | Path | None = None) -> tuple[dict[str, dict], list[dict]]:
    return list_local_resources("skill", work_dir)


def list_resources(repo: str, kind: str = "skill") -> tuple[dict[str, dict], list[dict]]:
    """Return valid remote resources and malformed-entry diagnostics."""
    logger.info("Listing remote library %ss", kind)
    with _checkout(repo) as checkout:
        root = _resources_root(checkout, kind)
        resources: dict[str, dict] = {}
        errors: list[dict] = []
        if not root.is_dir():
            return resources, errors

        for item in sorted(root.iterdir(), key=lambda path: path.name):
            if not item.is_dir() or item.is_symlink():
                continue
            try:
                info = _load_strict(item, kind)
                resources[item.name] = {"description": info["description"]}
                if kind == "skill":
                    resources[item.name]["frontmatter"] = info["frontmatter"]
            except LibraryError as exc:
                errors.append({"name": item.name, "reason": str(exc)})
        return resources, errors


def list_skills(repo: str) -> tuple[dict[str, dict], list[dict]]:
    return list_resources(repo, "skill")


def _remote_resource(checkout: Path, name: str, kind: str = "skill") -> Path:
    _, _, _, validator = _resource_config(kind)
    if not validator(name):
        raise LibraryError(f"invalid {kind} name: {name!r}")
    resource_dir = _resources_root(checkout, kind) / name
    if not resource_dir.is_dir():
        raise LibraryError(f"{kind} not found in library: {name}")
    _load_strict(resource_dir, kind)
    return resource_dir


def install_resource(
    repo: str,
    name: str,
    kind: str = "skill",
    work_dir: str | Path | None = None,
) -> Path:
    """Install one remote resource into the current project's ``.ene`` directory."""
    dirname, _, _, _ = _resource_config(kind)
    logger.info("Installing %s %s", kind, name)
    base = Path(work_dir) if work_dir is not None else Path.cwd()
    dest_root = get_ene_dir(base) / dirname
    dest = dest_root / name
    if dest.exists() or dest.is_symlink():
        raise LibraryError(f"local {kind} already exists: {dest}")

    with _checkout(repo) as checkout:
        source = _remote_resource(checkout, name, kind)
        dest_root.mkdir(parents=True, exist_ok=True)
        staging = dest_root / f".{name}.tmp-{uuid.uuid4().hex}"
        try:
            shutil.copytree(source, staging)
            os.replace(staging, dest)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    _record_sync(dest, _tree_digest(dest))
    return dest


def install_skill(repo: str, name: str, work_dir: str | Path | None = None) -> Path:
    return install_resource(repo, name, "skill", work_dir)


def update_resource(
    repo: str,
    name: str,
    kind: str = "skill",
    work_dir: str | Path | None = None,
    force: bool = False,
) -> str:
    """Synchronize one installed resource with its library copy.

    Returns ``"current"``, ``"pulled"``, or ``"pushed"``. Conflicts are
    rejected unless ``force`` replaces the library copy with the local tree.
    """
    if not isinstance(force, bool):
        raise LibraryError(f"invalid update force flag: {force!r}")
    dirname, _, _, validator = _resource_config(kind)
    logger.info("Updating %s %s", kind, name)
    base = Path(work_dir) if work_dir is not None else Path.cwd()
    dest = get_ene_dir(base) / dirname / name
    if not dest.is_dir() or dest.is_symlink():
        raise LibraryError(f"local project {kind} not found: {dest}")

    with _snapshot_resource(dest, kind) as snapshot:
        local_digest = _tree_digest(snapshot)
        with _checkout(repo) as checkout:
            if not validator(name):
                raise LibraryError(f"invalid {kind} name: {name!r}")
            source = _resources_root(checkout, kind) / name
            baseline = _sync_digest(snapshot)
            if not source.is_dir():
                if baseline is not None:
                    raise LibraryError(
                        f"cannot update '{name}': removed from library; restore it with "
                        "'ene lib upload' or remove the project copy with 'ene lib remove --local'"
                    )
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(snapshot, source)
                _record_sync(source, local_digest)
                _git("add", "--force", "--", f"{dirname}/{name}", cwd=checkout)
                _git("commit", "--quiet", "-m", f"{kind}: add {name}", cwd=checkout)
                _push_main(repo, checkout)
                action = "pushed"
            else:
                _load_strict(source, kind)
                remote_digest = _tree_digest(source)
                if local_digest == remote_digest:
                    if _record_sync(source, remote_digest):
                        _git("add", "--force", "--", f"{dirname}/{name}", cwd=checkout)
                        _git("commit", "--quiet", "-m", f"{kind}: track {name}", cwd=checkout)
                        _push_main(repo, checkout)
                    action = "current"
                elif baseline == remote_digest or (
                    baseline not in (local_digest, remote_digest) and force
                ):
                    shutil.rmtree(source)
                    shutil.copytree(snapshot, source)
                    _record_sync(source, local_digest)
                    _git("add", "--force", "--", f"{dirname}/{name}", cwd=checkout)
                    _git("commit", "--quiet", "-m", f"{kind}: update {name}", cwd=checkout)
                    _push_main(repo, checkout)
                    action = "pushed"
                elif baseline == local_digest:
                    if _tree_digest(dest) != local_digest:
                        raise LibraryError(
                            f"cannot update '{name}': local copy changed during update; retry"
                        )
                    if _record_sync(source, remote_digest):
                        _git("add", "--force", "--", f"{dirname}/{name}", cwd=checkout)
                        _git("commit", "--quiet", "-m", f"{kind}: track {name}", cwd=checkout)
                        _push_main(repo, checkout)
                    _replace_local_resource(source, dest, local_digest)
                    local_digest = remote_digest
                    action = "pulled"
                else:
                    reason = "both copies changed" if baseline is not None else "no sync history"
                    raise LibraryError(
                        f"cannot update '{name}': {reason}; merge both copies into the "
                        "local resource (ask your agent for help), then run "
                        f"'ene lib update {name} --force' to replace the library copy"
                    )

    _record_sync(dest, local_digest)
    return action


def update_skill(
    repo: str,
    name: str,
    work_dir: str | Path | None = None,
    force: bool = False,
) -> str:
    return update_resource(repo, name, "skill", work_dir, force)


def _replace_local_resource(source: Path, dest: Path, expected_digest: str) -> None:
    dest_root = dest.parent
    staging = dest_root / f".{dest.name}.tmp-{uuid.uuid4().hex}"
    backup = dest_root / f".{dest.name}.bak-{uuid.uuid4().hex}"
    try:
        shutil.copytree(source, staging)
        if _tree_digest(dest) != expected_digest:
            raise LibraryError(f"local copy changed during update: {dest.name}")
        os.replace(dest, backup)
        try:
            os.replace(staging, dest)
        except BaseException:
            os.replace(backup, dest)
            raise
        shutil.rmtree(backup)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


_SYNC_FILE = ".ene-lib.json"


def _tree_manifest(root: Path) -> dict[str, tuple[str, bool]]:
    manifest: dict[str, tuple[str, bool]] = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if not path.is_file() or relative == _SYNC_FILE:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        executable = bool(path.stat().st_mode & 0o111)
        manifest[relative] = (digest, executable)
    return manifest


def _same_tree(left: Path, right: Path) -> bool:
    return _tree_manifest(left) == _tree_manifest(right)


def _tree_digest(root: Path) -> str:
    manifest = _tree_manifest(root)
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _read_sync_metadata(path: Path) -> str | None:
    if not path.exists() and not path.is_symlink():
        return None
    if path.is_symlink() or not path.is_file():
        raise LibraryError(f"reserved sync metadata path is invalid: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LibraryError(f"reserved sync metadata is invalid: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise LibraryError(f"reserved sync metadata is invalid: {path}: expected a JSON object")
    digest = data.get("digest")
    if set(data) != {"ene", "digest"} or data.get("ene") != 1 or not isinstance(digest, str):
        raise LibraryError(
            f"reserved sync metadata is invalid: {path}: expected "
            '{"ene": 1, "digest": "..."}'
        )
    return digest


def _sync_digest(skill: Path) -> str | None:
    return _read_sync_metadata(skill / _SYNC_FILE)


def _record_sync(skill: Path, digest: str) -> bool:
    path = skill / _SYNC_FILE
    _sync_digest(skill)
    content = json.dumps({"ene": 1, "digest": digest}) + "\n"
    changed = not path.is_file() or path.read_text(encoding="utf-8") != content
    if changed:
        try:
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise LibraryError(f"cannot write reserved sync metadata: {path}: {exc}") from exc
    return changed


def remove_local_resource(
    name: str,
    kind: str = "skill",
    work_dir: str | Path | None = None,
) -> Path:
    dirname, _, _, validator = _resource_config(kind)
    logger.info("Removing local %s %s", kind, name)
    if not validator(name):
        raise LibraryError(f"invalid {kind} name: {name!r}")
    base = Path(work_dir) if work_dir is not None else Path.cwd()
    resource = get_ene_dir(base) / dirname / name
    if not resource.is_dir() or resource.is_symlink():
        raise LibraryError(f"local project {kind} not found: {resource}")
    shutil.rmtree(resource)
    return resource


def remove_local_skill(name: str, work_dir: str | Path | None = None) -> Path:
    return remove_local_resource(name, "skill", work_dir)


def remove_resource(repo: str, name: str, kind: str = "skill") -> str:
    """Remove a resource from the library and return the new commit hash."""
    dirname, _, _, validator = _resource_config(kind)
    logger.info("Removing remote %s %s", kind, name)
    if not validator(name):
        raise LibraryError(f"invalid {kind} name: {name!r}")

    with _checkout(repo) as checkout:
        dest = _resources_root(checkout, kind) / name
        if not dest.exists() and not dest.is_symlink():
            raise LibraryError(f"{kind} not found in library: {name}")

        _git("rm", "--quiet", "-r", "--", f"{dirname}/{name}", cwd=checkout)
        _git("commit", "--quiet", "-m", f"{kind}: remove {name}", cwd=checkout)
        commit = _git("rev-parse", "HEAD", cwd=checkout).stdout.strip()
        _push_main(repo, checkout)
        return commit


def remove_skill(repo: str, name: str) -> str:
    return remove_resource(repo, name, "skill")


def upload_resource(
    repo: str,
    name: str,
    kind: str = "skill",
    work_dir: str | Path | None = None,
    force: bool = False,
) -> str | None:
    """Commit and push a project resource to the library's main branch."""
    dirname, _, _, validator = _resource_config(kind)
    logger.info("Uploading %s %s", kind, name)
    if not validator(name):
        raise LibraryError(f"invalid {kind} name: {name!r}")
    base = Path(work_dir) if work_dir is not None else Path.cwd()
    source = get_ene_dir(base) / dirname / name
    if not source.is_dir():
        raise LibraryError(f"local project {kind} not found: {source}")
    with _snapshot_resource(source, kind) as snapshot:
        with _checkout(repo) as checkout:
            dest = _resources_root(checkout, kind) / name
            updating = dest.exists() or dest.is_symlink()
            if updating:
                _load_strict(dest, kind)
                if _same_tree(snapshot, dest):
                    digest = _tree_digest(snapshot)
                    remote_changed = _record_sync(dest, digest)
                    if remote_changed:
                        _git("add", "--force", "--", f"{dirname}/{name}", cwd=checkout)
                        _git("commit", "--quiet", "-m", f"{kind}: track {name}", cwd=checkout)
                        _push_main(repo, checkout)
                    _record_sync(source, digest)
                    return None
                if not force:
                    raise LibraryError(
                        f"{kind} already exists in library: {name} (use --force to update it)"
                    )
                shutil.rmtree(dest)

            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(snapshot, dest)
            digest = _tree_digest(snapshot)
            _record_sync(dest, digest)
            _git("add", "--force", "--", f"{dirname}/{name}", cwd=checkout)
            action = "update" if updating else "add"
            _git("commit", "--quiet", "-m", f"{kind}: {action} {name}", cwd=checkout)
            commit = _git("rev-parse", "HEAD", cwd=checkout).stdout.strip()
            _push_main(repo, checkout)
            _record_sync(source, digest)
            return commit


def upload_skill(
    repo: str,
    name: str,
    work_dir: str | Path | None = None,
    force: bool = False,
) -> str | None:
    return upload_resource(repo, name, "skill", work_dir, force)
