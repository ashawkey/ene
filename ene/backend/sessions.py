"""Session selection, branchable persistence, loading, and replay."""

import os
import shutil
import time
from dataclasses import asdict
from pathlib import Path

from rich.cells import cell_len, chop_cells
from rich.table import Table
from rich.text import Text

from ene.context import (
    CompactionState,
)
from ene.personas import get_persona
from ene.session_store import SessionStore
from ene.utils.rewind import CheckoutPlan

_OP_STYLES = {"create": "green", "modify": "yellow", "delete": "red"}

# Human labels for revisions saved by the agent rather than by a user prompt.
_REASON_LABELS = {
    "initial": "(session start)",
    "pre-compaction": "(before compaction)",
    "pre-rewind": "(before rewind)",
    "conversation-rewind": "(after conversation rewind)",
    "code-rewind": "(after code rewind)",
    "resume": "(before resuming another session)",
}


def _reason_label(reason: str) -> str:
    return _REASON_LABELS.get(reason, f"({reason})")


def _shorten(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if cell_len(text) <= width:
        return text
    if width == 1:
        return "…"
    return chop_cells(text, width - 1)[0] + "…"


def _session_choice_labels(rows: list[tuple[str, object, object, str]], width: int) -> list[str]:
    """Format aligned session metadata and use remaining line width for previews."""
    name_width = max(cell_len(name) for name, *_ in rows)
    message_width = max(len(str(message_count)) for _, message_count, _, _ in rows)
    round_width = max(len(str(round_id)) for _, _, round_id, _ in rows)
    labels = []
    for name, message_count, round_id, preview in rows:
        label = (
            f"{name}{' ' * (name_width - cell_len(name))}  "
            f"msgs:{str(message_count):>{message_width}}  rounds:{str(round_id):>{round_width}}"
        )
        separator = "  "
        preview_width = width - cell_len(label) - cell_len(separator)
        if preview and preview_width > 0:
            label += separator + _shorten(preview, preview_width)
        labels.append(label)
    return labels


def _file_label(count: int) -> str:
    return f"{count} file{'s' if count != 1 else ''}" if count else "no files"


def _line_label(added: int, removed: int) -> str:
    return " ".join(part for part in (f"+{added}" if added else "", f"-{removed}" if removed else "") if part)


def _relative_time(timestamp: float) -> str:
    seconds = max(0, int(time.time() - timestamp))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    if seconds < 7 * 86400:
        return f"{seconds // 86400}d ago"
    return time.strftime("%Y-%m-%d", time.localtime(timestamp))


class SessionMixin:
    SESSIONS_DIR_NAME = "sessions"
    REWIND_PROMPT_WIDTH = 56
    REWIND_MAX_FILES = 12
    REWIND_MAX_DROPPED = 6

    def _session_store_for(self, name: str) -> SessionStore:
        return SessionStore(self._sessions_dir(), name)

    def _pick_session(self) -> str | None:
        sessions_dir = self._sessions_dir()
        paths = sorted(
            (path for path in sessions_dir.iterdir() if path.is_dir() and (path / "history.jsonl").exists()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        paths = [path for path in paths if path.name != self._session_id]
        if not paths:
            self.console.system(f"No other saved sessions in {sessions_dir}")
            return None

        rows: list[tuple[str, object, object, str]] = []
        for path in paths:
            name = path.name
            try:
                meta = SessionStore.load_summary(sessions_dir, name)
                display_name = f"{meta['session_name']} ({name})" if meta.get("session_name") else name
                row = (
                    display_name,
                    meta["message_count"],
                    meta["round_id"],
                    meta["last_user_message"],
                )
            except Exception:
                row = (name, "?", "?", "unreadable")
            rows.append(row)

        # Leave room for questionary's selection indicator and padding.
        labels = _session_choice_labels(rows, max(1, self.console.width - 4))
        names = [path.name for path in paths]
        picked = self.console.select(message="Pick a session to resume", choices=labels)
        if picked is None:
            return None
        return names[labels.index(picked)]

    def _cmd_resume(self, raw: str):
        parts = raw.split(maxsplit=1)
        target = parts[1].strip() if len(parts) > 1 else self._pick_session()
        if target is None:
            self.console.system("Resume cancelled.")
            return
        try:
            target_store = self._session_store_for(target)
        except ValueError as e:
            self.console.error(str(e))
            return
        if not target_store.exists:
            self.console.error(f"Session not found: {target}")
            return
        if target == self._session_id:
            self.console.system(f"Already in session '{target}'.")
            return

        if self._session_id and self.context.messages:
            try:
                self.save_session(self._session_id, reason="resume")
            except Exception as e:
                self.console.warn(f"Could not save current session: {e}")

        old_id = self._session_id
        old_name = getattr(self, "session_name", "")
        try:
            target_name = SessionStore.load_summary(
                self._sessions_dir(), target
            ).get("session_name", "")
        except (OSError, ValueError):
            target_name = ""
        callback = getattr(self, "_session_changed", None)
        try:
            if callback is not None:
                callback(target, target_name)
        except Exception as exc:
            self.console.error(str(exc))
            return
        self.session_name = ""
        if not self.load_session(target):
            self._session_id = old_id
            self.session_name = old_name
            if callback is not None:
                try:
                    callback(old_id, old_name)
                except Exception:
                    pass
            return
        self._session_id = target
        self.tool_executor.shutdown_processes(clear=True)
        self._install_change_tracker()

    def _cmd_name(self, raw: str):
        parts = raw.split(maxsplit=1)
        if len(parts) == 1:
            current = getattr(self, "session_name", "")
            self.console.system(
                f"Session name: {current}" if current else "This session is unnamed."
            )
            return
        requested = parts[1].strip()
        old_name = getattr(self, "session_name", "")
        try:
            callback = getattr(self, "_session_name_changed", None)
            if callback is not None:
                requested = callback(requested)
        except Exception as exc:
            self.console.error(str(exc))
            return
        self.session_name = requested
        try:
            store = getattr(self, "_session_store", None)
            if store is None or store.session_id != self._session_id:
                store = self._session_store_for(self._session_id)
                self._session_store = store
            store.rename(requested)
        except Exception as exc:
            self.session_name = old_name
            if callback is not None:
                try:
                    callback(old_name)
                except Exception:
                    pass
            self.console.error(f"Could not save session name: {exc}")
            return
        self.presence.update(session=requested)
        if requested:
            self.console.system(f"Session named '{requested}'.")
        else:
            self.console.system("Session name cleared.")

    def _cmd_rewind(self):
        """Move to the checkpoint before a prompt; subsequent work branches."""
        if not self._session_id or not self.changes or self._session_store is None:
            self.console.warn("Rewind is only available in interactive chat mode with a session.")
            return

        store = self._session_store
        try:
            self.save_session(self._session_id, reason="pre-rewind")
        except Exception as e:
            self.console.error(f"Could not checkpoint current state: {e}")
            return

        candidates = store.candidates()
        if not any(not candidate["current"] for candidate in candidates):
            self.console.system("No earlier revisions to rewind to.")
            return

        candidate = self._pick_revision(candidates)
        if candidate is None:
            self.console.system("Rewind cancelled.")
            return

        target_id = candidate["id"]
        if candidate["current"]:
            self.console.system("That revision is already checked out.")
            return

        target = store.materialize(target_id)
        plan = self.changes.plan_checkout(target.get("code_revision_id"))
        self._print_rewind_preview(target_id, target, plan, candidate["prompt"])

        mode = self._pick_rewind_mode(target, plan)
        if mode is None:
            self.console.system("Rewind cancelled.")
            return

        if mode == "code":
            self._apply_code_plan(plan, target_id)
            self.save_session(self._session_id, reason="code-rewind")
            # The conversation is untouched, so the transcript on screen still
            # matches it — redrawing would only erase the preview above.
            self.console.system(
                f"Code checked out from revision {target_id[:10]}; conversation unchanged. "
                f"New work will branch from revision {self._session_revision_id[:10]}."
            )
            return

        if mode == "both":
            self._apply_code_plan(plan, target_id)
            data = store.checkout(target_id)
            self._session_revision_id = target_id
            self._restore_session_data(data)
        else:
            self._restore_session_data(target)
            self._session_revision_id = target_id
            self.save_session(self._session_id, reason="conversation-rewind")

        self._set_rewind_draft(candidate["prompt"])
        self.console.reset_timeline()
        self._replay_context()
        self.console.system(
            f"Checked out revision {self._session_revision_id[:10]} at round {self.round_id}. "
            "The selected prompt is ready to edit; new work will branch from here."
        )

    def _cmd_fork(self, raw: str) -> None:
        """Start a new session at an earlier prompt boundary."""
        if not self._session_id or not self.changes or self._session_store is None:
            self.console.warn("Fork is only available in interactive chat mode with a session.")
            return

        parts = raw.split(maxsplit=1)
        requested_name = parts[1].strip() if len(parts) > 1 else ""
        source_store = self._session_store
        source_id = self._session_id
        source_name = getattr(self, "session_name", "")
        try:
            self.save_session(source_id, reason="pre-fork")
        except Exception as exc:
            self.console.error(f"Could not checkpoint current state: {exc}")
            return

        source_data = source_store.materialize()
        candidates = source_store.candidates()
        if not any(not candidate["current"] for candidate in candidates):
            self.console.system("No earlier revisions to fork from.")
            return

        candidate = self._pick_revision(candidates, action="fork")
        if candidate is None:
            self.console.system("Fork cancelled.")
            return

        target = source_store.materialize(candidate["id"])
        target.pop("revision_id", None)
        target.pop("code_revision_id", None)
        target["session_name"] = requested_name
        session_id = self._reserve_session_id()
        target_store = self._session_store_for(session_id)
        try:
            target_store.commit(
                target,
                parent_id=None,
                code_parent_id=None,
                changes=[],
                reason="initial",
                session_name=requested_name,
            )
        except Exception as exc:
            shutil.rmtree(target_store.path, ignore_errors=True)
            self.console.error(f"Could not create fork: {exc}")
            return

        callback = getattr(self, "_session_changed", None)
        try:
            if callback is not None:
                callback(session_id, requested_name)
        except Exception as exc:
            shutil.rmtree(target_store.path, ignore_errors=True)
            self.console.error(str(exc))
            return

        self.session_name = requested_name
        try:
            self._restore_session_data(target)
        except Exception as exc:
            self.session_name = source_name
            try:
                self._restore_session_data(source_data)
            except Exception:
                pass
            if callback is not None:
                try:
                    callback(source_id, source_name)
                except Exception:
                    pass
            shutil.rmtree(target_store.path, ignore_errors=True)
            self.console.error(f"Could not restore fork: {exc}")
            return

        self._session_id = session_id
        self._session_store = target_store
        self._session_revision_id = target_store.head_id
        self._pending_images.clear()
        self.tool_executor.shutdown_processes(clear=True)
        self._install_change_tracker()
        self._set_rewind_draft(candidate["prompt"])
        self.console.reset_timeline()
        self._replay_context()
        name_note = f" named '{requested_name}'" if requested_name else ""
        self.console.system(
            f"Forked session '{source_id}' at round {self.round_id} into "
            f"new session '{session_id}'{name_note}. The selected prompt is ready to edit."
        )

    def _apply_code_plan(self, plan: CheckoutPlan, target_id: str) -> None:
        """Move files to the planned state; this is what advances the code revision."""
        operations = self.changes.apply_plan(plan)
        if plan:
            self.console.system(
                f"Code moved to revision {target_id[:10]}: "
                f"{plan.files} file(s) changed, {operations} operation(s) applied."
            )
        else:
            self.console.system("Code already matched that revision; no files changed.")

    def _pick_revision(
        self, candidates: list[dict], *, action: str = "rewind"
    ) -> dict | None:
        """Select a prompt boundary, showing the most recent prompt first."""
        ordered_candidates = list(
            reversed([candidate for candidate in candidates if not candidate["current"]])
        )

        labels = []
        for index, revision in enumerate(ordered_candidates, start=1):
            round_label = f"round {revision['round_id']}"
            prompt = revision["prompt"]
            if revision["reason"] == "initial":
                prompt += f"  {_reason_label('initial')}"
            labels.append(
                f"{index:>2}. {round_label:<9} · {revision['reason']:<11} · "
                f"{_relative_time(revision['created_at']):<9} · {_file_label(revision['files']):<8} · "
                f"{_shorten(prompt, self.REWIND_PROMPT_WIDTH)}"
            )
        message = (
            "Pick a prompt to rewind to"
            if action == "rewind"
            else "Pick a prompt to fork from"
        )
        picked = self.console.select(message=message, choices=labels)
        if picked is None:
            return None
        return ordered_candidates[labels.index(picked)]

    def _set_rewind_draft(self, prompt: str) -> None:
        """Put the selected, now-removed prompt back in terminal and web editors."""
        self._rewind_draft = prompt
        events = getattr(self, "events", None)
        if events is not None:
            events.publish("draft_set", text=prompt)

    def _print_rewind_preview(
        self, target_id: str, target: dict, plan: CheckoutPlan, prompt: str
    ) -> None:
        """Show what checking out *target_id* would do to history and to files."""
        store = self._session_store
        revision = store.revisions[target_id]
        detail = Table.grid(padding=(0, 1))
        detail.add_column(width=12, style="dim", no_wrap=True)
        detail.add_column(overflow="fold")

        detail.add_row(
            "Revision",
            Text(
                f"{target_id[:10]}  ·  round {target.get('round_id', 0)}  ·  "
                f"{_relative_time(revision['createdAt'])}  ·  saved as {revision['reason']}"
            ),
        )
        detail.add_row("Prompt", Text(prompt))
        detail.add_row(
            "Conversation",
            Text(
                f"{len(self.context.messages)} → {len(target['messages'])} messages, "
                f"round {self.round_id} → {target.get('round_id', 0)}"
            ),
        )
        for line, style in self._dropped_rounds(target_id):
            detail.add_row("", Text(line, style=style))

        if plan:
            change = "would change" if plan.dirty else "will change"
            detail.add_row(
                "Files",
                Text(f"{plan.files} {change}  (+{plan.added} / -{plan.removed})", style="yellow"),
            )
            for delta in plan.deltas[: self.REWIND_MAX_FILES]:
                detail.add_row(
                    "",
                    Text(f"{delta.op:<7} {delta.path}", style=_OP_STYLES[delta.op]).append(
                        f"   {_line_label(*store.text_stats(delta.before, delta.after))}", style="dim"
                    ),
                )
            hidden = plan.files - self.REWIND_MAX_FILES
            if hidden > 0:
                detail.add_row("", Text(f"… and {hidden} more file(s)", style="dim"))
            if plan.dirty:
                detail.add_row(
                    "",
                    Text(
                        f"! Code cannot be reverted because {len(plan.dirty)} file(s) changed "
                        f"on disk since they were recorded: {', '.join(plan.dirty[:5])}",
                        style="bold red",
                    ),
                )
        else:
            detail.add_row("Files", Text("no files will change", style="green"))

        self.console.print(detail)

    def _dropped_rounds(self, target_id: str) -> list[tuple[str, str]]:
        """Describe the rounds between the current revision and *target_id*."""
        store = self._session_store
        chain = store.revision_ancestors(self._session_revision_id)
        if target_id not in chain:
            return [("target is on a different branch — messages are replaced, not truncated", "yellow")]

        # Several revisions can share a round (autosave, pre-compaction, pre-rewind);
        # the target's own round is not "dropped", only the rounds built on top of it.
        target_round = store.revisions[target_id]["state"].get("round_id", 0)
        seen: set[int] = {target_round}
        dropped: list[tuple[int, str]] = []
        for revision_id in chain[: chain.index(target_id)]:
            round_id = store.revisions[revision_id]["state"].get("round_id", 0)
            if round_id in seen:
                continue
            seen.add(round_id)
            dropped.append((round_id, store.revision_prompt(revision_id)))
        if not dropped:
            return []

        lines = [(f"{len(dropped)} round(s) will be dropped:", "dim")]
        for round_id, prompt in dropped[: self.REWIND_MAX_DROPPED]:
            lines.append((f"  round {round_id}  {_shorten(prompt, self.REWIND_PROMPT_WIDTH)}", "dim"))
        if len(dropped) > self.REWIND_MAX_DROPPED:
            lines.append((f"  … and {len(dropped) - self.REWIND_MAX_DROPPED} more", "dim"))
        return lines

    def _pick_rewind_mode(self, target: dict, plan: CheckoutPlan) -> str | None:
        """Select a rewind mode from options labelled with their actual effect.

        Returns ``"both"``, ``"conversation"``, ``"code"``, or ``None`` to cancel.
        """
        conversation = f"{len(self.context.messages)} → {len(target['messages'])} messages"
        if not plan or plan.dirty:
            modes = ["conversation"]
            choices = [
                f"1. Conversation only — {conversation}, files untouched",
                "2. Cancel",
            ]
        else:
            code = f"{plan.files} file(s) reverted (+{plan.added} / -{plan.removed})"
            modes = ["both", "conversation", "code"]
            choices = [
                f"1. Conversation + code — {conversation}, {code}",
                f"2. Conversation only — {conversation}, files untouched",
                f"3. Code only — conversation kept, {code}",
                "4. Cancel",
            ]
        picked = self.console.select(message="Choose rewind mode", choices=choices)
        if picked is None:
            return None
        index = choices.index(picked)
        return modes[index] if index < len(modes) else None

    def _sessions_dir(self) -> Path:
        directory = self._ene_dir() / self.SESSIONS_DIR_NAME
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _session_data(self) -> dict:
        return {
            "model": self.model,
            "model_alias": self.model_alias,
            "provider": self.provider_name,
            "round_id": self.round_id,
            "token_totals": self.token_totals,
            "tool_compaction_totals": self.tool_compaction_totals,
            "compaction_totals": self.compaction_totals,
            "system_prompt": self.context.system_prompt.to_wire(),
            "persona": self.persona.name,
            "persona_digest": self.persona.digest,
            "session_name": getattr(self, "session_name", ""),
            "loaded_skills": sorted(self.tool_executor._loaded_skills),
            "skill_loads": self.tool_executor._skill_loads,
            "messages": self.context.messages,
            "compaction_state": asdict(self.context.compaction_state),
        }

    def save_session(self, name: str | None = None, *, reason: str = "autosave") -> Path:
        name = name or self._session_timestamp()
        if self._session_store is None or self._session_store.session_id != name:
            self._session_store = self._session_store_for(name)
            self._session_revision_id = self._session_store.head_id

        changes = self.changes.pending_changes if self.changes and self.changes.session_id == name else []
        code_parent = self.changes.code_revision_id if self.changes and self.changes.session_id == name else None
        revision_id, code_revision_id, _ = self._session_store.commit(
            self._session_data(),
            parent_id=self._session_revision_id,
            code_parent_id=code_parent,
            changes=changes,
            reason=reason,
            session_name=getattr(self, "session_name", ""),
        )
        self._session_revision_id = revision_id
        if self.changes and self.changes.session_id == name:
            self.changes.mark_committed(code_revision_id)
        return self._session_store.history_path

    def load_session(self, name: str) -> bool:
        try:
            store = self._session_store_for(name)
            if not store.exists:
                self.console.error(f"Session not found: {name}")
                return False
            data = store.materialize()
            data["session_name"] = store.summary().get("session_name", "")
        except (OSError, ValueError) as e:
            self.console.error(f"Failed to read session: {e}")
            return False

        try:
            self._restore_session_data(data)
        except ValueError as e:
            self.console.error(f"Failed to restore session: {e}")
            return False

        self._session_store = store
        self._session_revision_id = store.head_id
        self._session_id = name
        self.console.system(
            f"Loaded session '{name}' ({len(self.context.messages)} messages, round {self.round_id}, "
            f"revision {self._session_revision_id[:10]})"
        )
        self._replay_context()
        return True

    def _restore_session_data(self, data: dict) -> None:
        saved_model = data.get("model", "")
        saved_provider = data.get("provider", "openai")
        if saved_model != self.model or saved_provider != self.provider_name:
            self.console.system(
                f"Note: session was saved with '{saved_provider}/{saved_model}', "
                f"current model is '{self.provider_name}/{self.model}'"
            )

        saved_persona = data.get("persona")
        if saved_persona:
            persona = get_persona(saved_persona, personas=self.personas)
            saved_digest = data.get("persona_digest")
            if saved_digest and saved_digest != persona.digest:
                self.console.warn(
                    f"Persona '{saved_persona}' changed since this session was saved."
                )
            if persona != self.persona:
                self.persona = persona
                self.system_prompt = self._build_system_prompt()
                self.context.system_prompt.content = self.system_prompt

        self.context.replace_messages(data["messages"])
        # Sessions saved before compaction state was carried structurally simply
        # start empty; the next pass rebuilds it from whatever history remains.
        carried = data.get("compaction_state") or {}
        self.context.compaction_state = CompactionState(
            original_request=carried.get("original_request", ""),
            read_files=tuple(carried.get("read_files", ())),
            modified_files=tuple(carried.get("modified_files", ())),
            skills=tuple(carried.get("skills", ())),
        )
        self.round_id = data.get("round_id", 0)
        if not getattr(self, "session_name", ""):
            self.session_name = data.get("session_name", "")
        self.token_totals.update(data.get("token_totals") or {})
        self.tool_compaction_totals.update(data.get("tool_compaction_totals") or {})
        self.compaction_totals.update(data.get("compaction_totals") or {})

        available = set(self.skills)
        self.tool_executor._loaded_skills = {
            name for name in data.get("loaded_skills", []) if name in available
        }
        self.tool_executor.reset_skill_tools()
        for name in list(self.tool_executor._loaded_skills):
            error = self.tool_executor._register_skill_tools(name, self.skills[name].get("dir"))
            if error is not None:
                self.console.warn(error)
                self.tool_executor._loaded_skills.discard(name)
        self.tool_executor._skill_loads = {
            name: count for name, count in (data.get("skill_loads") or {}).items()
            if isinstance(count, int)
        }


    def _install_change_tracker(self) -> None:
        work_dir = self.tool_executor._work_dir or os.getcwd()
        if self.changes is not None:
            self.changes.close()
        code_revision_id = None
        if self._session_store and self._session_store.exists:
            code_revision_id = self._session_store.materialize().get("code_revision_id")
        self.changes = self._create_change_tracker(
            self._session_id, work_dir, self._session_store, code_revision_id
        )
        self.tool_executor._change_tracker = self.changes
        self.tool_executor._get_round_id = lambda: self.round_id

    def _replay_context(self):
        """Replay the full conversation with omitted messages marked in place."""
        from ene.context import SUMMARY_MARKER
        from ene.replay import HiddenMessages, compact_replay, hidden_message

        msgs = compact_replay(
            self.context.messages,
            is_user=lambda msg: (
                msg.is_user
                and not msg.text.startswith(SUMMARY_MARKER)
            ),
            is_assistant=lambda msg: msg.is_assistant,
            has_text=lambda msg: bool(msg.text.strip()),
        )
        if not msgs:
            return

        self.console.system("── Session context (replay) ──")
        for msg in msgs:
            if isinstance(msg, HiddenMessages):
                self.console.system(hidden_message(msg.count))
            elif msg.is_user:
                self.console.user_input(msg.display)
            else:
                self.console.response(msg.text)
        self.console.system("── End of replay ──")

