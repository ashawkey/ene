"""Mutual awareness between ene agents sharing one workspace.

Each agent publishes a small record under ``.ene/agents/`` and reads its peers'
records from the same directory. This exists so an agent that meets an
unexpected file change or a transient test failure knows another agent may have
caused it, instead of investigating or reverting work that is not its own.

Records are advisory: they carry no locks and grant no exclusivity. A record
whose process is gone is reaped by whoever notices it, so a force-killed agent
cannot keep announcing itself.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .paths import get_ene_dir
from .process import process_exited

PRESENCE_DIR_NAME = "agents"

# Injected once, as an ordinary conversation message, when peers first appear.
# Phrased as a status notice because the model would otherwise read a bare
# statement in the user turn as a fresh instruction; the same problem the
# compaction handoff message solves the same way.
PEER_NOTICE = (
    "[workspace notice] Other ene agents are working in this workspace "
    "concurrently. This is an automated status notice, not an instruction from "
    "the user.\n"
    "Their file edits, new files, and transient test or build failures are "
    "expected. Ignore changes unrelated to your task instead of investigating "
    "or reverting them, and re-read a file before editing it when another agent "
    "may have changed it."
)


@dataclass(frozen=True)
class AgentPeer:
    """Another agent's published record."""

    agent_id: str
    pid: int
    model: str
    persona: str
    session: str
    started_at: float


class WorkspacePresence:
    """This agent's presence record plus a view of its live peers."""

    def __init__(
        self,
        work_dir: str | Path,
        *,
        model: str = "",
        persona: str = "",
        session: str = "",
    ):
        # Deliberately no I/O here: constructing an agent must not leave a
        # record behind in a workspace where no round ever runs.
        self.work_dir = str(work_dir)
        self.agent_id = uuid.uuid4().hex
        self.model = model
        self.persona = persona
        self.session = session
        self.started_at = time.time()
        self._path: Path | None = None

    def update(self, **fields: str) -> None:
        """Update published fields; the next refresh writes them."""
        for name, value in fields.items():
            if name not in ("model", "persona", "session"):
                raise ValueError(f"unknown presence field: {name}")
            setattr(self, name, value)

    def refresh(self) -> list[AgentPeer]:
        """Republish this agent's record and return the live peers it can see.

        Republishing every time keeps the record self-healing: ``ene clean`` or
        any other removal of the directory costs at most one round of mutual
        visibility rather than hiding the agent for the rest of its life.
        """
        directory = self._dir()
        if directory is None:
            return []
        self._publish(directory)
        return self._peers(directory)

    def peers(self) -> list[AgentPeer]:
        """Return the live peers without publishing this agent's own record.

        For read-only callers such as status probes, which may run far more
        often than rounds do.
        """
        directory = self._dir()
        if directory is None:
            return []
        return self._peers(directory)

    def close(self) -> None:
        """Withdraw this agent's record."""
        if self._path is None:
            return
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            pass
        self._path = None

    # -- internals ----------------------------------------------------------

    def _dir(self) -> Path | None:
        try:
            directory = get_ene_dir(self.work_dir) / PRESENCE_DIR_NAME
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            # A read-only or otherwise unusable workspace costs awareness, not
            # correctness: the agent simply sees no peers.
            return None
        return directory

    def _publish(self, directory: Path) -> None:
        path = directory / f"{self.agent_id}.json"
        record = {
            "agent_id": self.agent_id,
            "pid": os.getpid(),
            "model": self.model,
            "persona": self.persona,
            "session": self.session,
            "started_at": self.started_at,
        }
        staging = directory / f".{self.agent_id}.{uuid.uuid4().hex}.tmp"
        try:
            staging.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            if os.name == "posix":
                staging.chmod(0o600)
            # Atomic replace, so a concurrent reader never sees a partial record.
            os.replace(staging, path)
        except OSError:
            staging.unlink(missing_ok=True)
            return
        self._path = path

    def _peers(self, directory: Path) -> list[AgentPeer]:
        peers: list[AgentPeer] = []
        try:
            paths = sorted(directory.glob("*.json"))
        except OSError:
            return peers
        for path in paths:
            if path.name == f"{self.agent_id}.json":
                continue
            peer = _read_peer(path)
            if peer is None or process_exited(peer.pid):
                _reap(path)
                continue
            peers.append(peer)
        return sorted(peers, key=lambda peer: peer.started_at)


def _read_peer(path: Path) -> AgentPeer | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict):
        return None
    try:
        pid = int(record.get("pid", 0))
        started_at = float(record.get("started_at", 0))
    except (TypeError, ValueError):
        return None
    agent_id = record.get("agent_id")
    if not isinstance(agent_id, str) or not agent_id or pid <= 0:
        # An unusable PID cannot be probed for liveness, so treat the whole
        # record as junk and let the caller reap it.
        return None
    return AgentPeer(
        agent_id=agent_id,
        pid=pid,
        model=str(record.get("model", "")),
        persona=str(record.get("persona", "")),
        session=str(record.get("session", "")),
        started_at=started_at,
    )


def _reap(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
