"""Interactive slash commands for :class:`LLMAgent`."""

from contextlib import nullcontext

from rich.markup import escape

from ene.context import (
    SUMMARY_MARKER,
    build_tool_name_index,
    get_display_text,
    get_role,
    get_text,
    get_tool_call_id,
    get_tool_calls,
    msg_chars,
)
from ene.models import REASONING_EFFORTS, resolve_model_profile
from ene.providers import (
    AuthInteraction,
    CompletionRequest,
    ProviderSettings,
    create_provider,
)
from ene.personas import DEFAULT_PERSONA, PersonaInfo, discover_personas, get_persona
from ene.utils.interrupt import RequestInterrupted, run_interruptible


_RECAP_INPUT_MAX_CHARS = 24_000
_RECAP_MAX_OUTPUT_TOKENS = 128
_RECAP_TIMEOUT_SECONDS = 60


class AgentCommandsMixin:
    def _round_timer(self):
        timer = getattr(self.console, "round_timer", None)
        return timer() if timer is not None else nullcontext()

    # Single source of truth for slash commands: name → help line.
    # Drives dispatch (COMMANDS), /help output, and terminal auto-completion.
    COMMAND_HELP = {
        "help": "Show this help message",
        "context": "List context messages; /context <id> shows one in full",
        "system_prompt": "Print the current full system prompt",
        "compact": "Force context compaction via LLM summarization",
        "recap": "Summarize the conversation's task in one sentence",
        "continue": "Resume an unfinished round without adding a user message",
        "usage": "Show token usage for this session",
        "ps": "List background processes; /ps <process-id|pid> shows its recent log",
        "model": "Show or switch LLM model (/model <name>)",
        "login": "Log in to an OAuth provider (/login [provider|model-alias])",
        "logout": "Remove stored OAuth credentials (/logout [provider|model-alias])",
        "auth": "Show authentication status (/auth [provider|model-alias])",
        "effort": "Show or set reasoning effort (/effort <level>)",
        "skills": "List skills; /skills <name> to load one, /skills reload to re-scan",
        "persona": "List/switch personas; /persona reload to re-scan",
        "wait": "Send a prompt after a delay (/wait <30s|5m|1h> <prompt>)",
        "rewind": "Return to before a user prompt, edit it, then branch",
        "clear": "Clear conversation history (keep system prompt)",
        "resume": "Save current, then resume a previous session (/resume [session_id])",
        "exit": "Exit the agent (also: /quit)",
        "quit": "Exit the agent (alias of /exit)",
    }
    COMMANDS = set(COMMAND_HELP)

    # Commands that answer while an agent round is in flight instead of queueing
    # behind it. A round owns the conversation, the provider, and the terminal
    # prompt, so only commands that read session state or take effect on the
    # *next* API call qualify.
    INSTANT_COMMANDS = frozenset({
        "help", "usage", "ps", "context", "system_prompt", "auth", "effort",
    })
    # The same, but only in their bare listing form: given an argument these
    # switch model or persona, or load a skill into the running conversation.
    INSTANT_LISTING_COMMANDS = frozenset({"model", "persona", "skills"})

    def is_instant_command(self, raw: str) -> bool:
        """Whether ``raw`` can run now rather than queue behind a live round."""
        parts = raw.split(maxsplit=1)
        cmd = parts[0][1:].lower()
        if cmd in self.COMMANDS:
            if cmd in self.INSTANT_COMMANDS:
                return True
            return cmd in self.INSTANT_LISTING_COMMANDS and len(parts) == 1
        if cmd in getattr(self, "skills", {}):
            # Explicit skill invocation starts a model round.
            return False
        # A typo is worth reporting immediately rather than after the round.
        return True

    def _handle_command(self, raw: str) -> bool:
        """Handle a /command.  Returns True if the agent loop should stop."""
        cmd = raw.split()[0][1:].lower()

        if cmd in ("exit", "quit"):
            return True

        if cmd == "help":
            self._cmd_help()
        elif cmd == "compact":
            self._cmd_compact()
        elif cmd == "recap":
            self._cmd_recap(raw)
        elif cmd == "continue":
            self._cmd_continue(raw)
        elif cmd == "usage":
            self._cmd_usage()
        elif cmd == "ps":
            self._cmd_ps(raw)
        elif cmd == "clear":
            self._cmd_clear()
        elif cmd == "resume":
            self._cmd_resume(raw)
        elif cmd == "model":
            self._cmd_model(raw)
        elif cmd == "login":
            self._cmd_login(raw)
        elif cmd == "logout":
            self._cmd_logout(raw)
        elif cmd == "auth":
            self._cmd_auth(raw)
        elif cmd == "effort":
            self._cmd_effort(raw)
        elif cmd == "context":
            self._cmd_context(raw)
        elif cmd == "system_prompt":
            self._cmd_system_prompt()
        elif cmd == "rewind":
            self._cmd_rewind()
        elif cmd == "skills":
            self._cmd_skills(raw)
        elif cmd == "persona":
            self._cmd_persona(raw)

        return False

    def _slash_command_help(self) -> dict[str, str]:
        """Return built-in commands plus currently discovered skills."""
        commands = dict(self.COMMAND_HELP)
        for name, info in getattr(self, "skills", {}).items():
            if name not in commands:
                commands[name] = f"Skill — {info.get('description', '')}"
        return commands

    def _refresh_slash_commands(self) -> None:
        """Refresh completion data without replacing the terminal's shared dict."""
        commands = self._slash_command_help()
        if hasattr(self, "_slash_commands"):
            self._slash_commands.clear()
            self._slash_commands.update(commands)
        else:
            self._slash_commands = commands

    def _cmd_help(self):
        width = max(len(name) for name in self.COMMAND_HELP) + 1  # +1 for the slash
        lines = ["  [cyan]!<cmd>[/cyan]" + " " * (width - 5) + "— Run a shell command directly (e.g. !ls, !git diff)"]
        for name, desc in self.COMMAND_HELP.items():
            if name == "quit":  # alias of /exit, already mentioned there
                continue
            lines.append(f"  [cyan]/{name}[/cyan]{' ' * (width - len(name))}— {desc}")
        self.console.print(
            "[bold blue]Available commands:[/bold blue]\n"
            "\n"
            + "\n".join(lines)
            + "\n\n"
            "  Press [bold]Enter[/bold] to send, [bold]Escape → Enter[/bold] for a newline.\n"
            "  Invoke a discovered skill with [cyan]/<skill-name> [optional task][/cyan].\n"
            "  While the agent is working, messages queue; commands that do not touch\n"
            "  the conversation (such as /usage and /context) run right away."
        )

    def _cmd_compact(self):
        if len(self.context.messages) <= 2:
            self.console.system("Not enough messages to compact.")
            return
        # Inside an operation so Escape can cancel the summarization round-trip;
        # CancellationToken.cancel() is a no-op when no operation is active.
        with self._operation("manual compaction"):
            try:
                self._run_compaction("Manual compaction requested")
            except RequestInterrupted:
                self.console.system("Compaction cancelled.")

    def _recap_input(self) -> str:
        """Render a bounded, user-focused view of the current conversation."""
        requests: list[str] = []
        original = self.context.compaction_state.original_request.strip()
        if original:
            requests.append(original)

        for message in self.context.messages:
            if get_role(message) != "user" or get_text(message).startswith(SUMMARY_MARKER):
                continue
            text = get_display_text(message).strip()
            if text and text not in requests:
                requests.append(text)

        if not requests:
            return ""

        opening_limit = (
            _RECAP_INPUT_MAX_CHARS // 2 if len(requests) > 1
            else _RECAP_INPUT_MAX_CHARS
        )
        opening = requests[0][:opening_limit]
        remaining = _RECAP_INPUT_MAX_CHARS - len(opening)
        recent: list[str] = []
        for text in reversed(requests[1:]):
            if remaining <= 0:
                break
            kept = text[:remaining]
            if kept:
                recent.append(kept)
                remaining -= len(kept)

        parts = [f"Opening request:\n{opening}"]
        if recent:
            parts.append(
                "Later user requests (chronological):\n"
                + "\n\n".join(reversed(recent))
            )
        return "\n\n".join(parts)

    def _recap_provider(self):
        """Return the configured recap provider, model, and ownership flag."""
        from ene.config import conf

        alias = conf.get("recap_model")
        if alias is None:
            return self.provider, self.model, False
        if not isinstance(alias, str) or not alias.strip():
            raise ValueError("'recap_model' must be a non-empty model alias")
        alias = alias.strip()
        model_conf = conf.get("openai", {}).get(alias)
        if not isinstance(model_conf, dict):
            raise ValueError(
                f"Recap model '{alias}' not found under 'openai' in the configuration"
            )
        model = model_conf.get("model", alias)
        profile = resolve_model_profile(model, alias)
        provider_name = model_conf.get("provider", "openai")
        provider = create_provider(provider_name, ProviderSettings(
            api_key=model_conf.get("api_key", ""),
            base_url=model_conf.get("base_url", ""),
            reasoning_style=profile.reasoning,
        ))
        return provider, model, True

    def _cmd_recap(self, raw: str = "/recap") -> None:
        """Summarize the user-authored task without changing conversation state."""
        if len(raw.split()) != 1:
            self.console.warn("Usage: /recap")
            return
        recap_input = self._recap_input()
        if not recap_input:
            self.console.system("There is no conversation to recap yet.")
            return

        provider = None
        owned = False
        try:
            provider, model, owned = self._recap_provider()
            prompt = (
                "Write exactly one concise plain-text sentence that reminds the user "
                "what this conversation is about and what task they are trying to "
                "complete. Base it primarily on the user's requests below. Do not "
                "mention tools, implementation process, this instruction, or that you "
                "are summarizing a conversation.\n\n"
                f"{recap_input}"
            )
            with self._operation("conversation recap"):
                with self.console.thinking(label="Recapping", progress=True):
                    result = run_interruptible(
                        lambda: provider.complete(CompletionRequest(
                            model=model,
                            messages=[{"role": "user", "content": prompt}],
                            stream=False,
                            max_output_tokens=_RECAP_MAX_OUTPUT_TOKENS,
                            reasoning_effort="low",
                            timeout=_RECAP_TIMEOUT_SECONDS,
                        )),
                        self.cancellation,
                        on_cancel=provider.cancel,
                    )
            if self.cancellation is not None and self.cancellation.cancelled:
                raise RequestInterrupted()
            if result.usage is not None:
                self._accumulate_usage(result.usage)
            recap = " ".join(get_text(result.message).split())
            if not recap:
                raise RuntimeError("Recap provider returned no text")
            self.console.print(f"Recap: {recap}", markup=False)
        except RequestInterrupted:
            self.console.system("Recap cancelled.")
        except Exception as e:
            self.console.warn(f"Could not generate recap: {e}")
        finally:
            if owned and provider is not None:
                provider.close()

    def _cmd_continue(self, raw: str = "/continue"):
        """Resume a transcript that is waiting for an assistant response."""
        if len(raw.split()) != 1:
            self.console.warn("Usage: /continue")
            return

        if not self.context.messages:
            self.console.warn("There is no unfinished round to continue.")
            return

        messages = self.context.messages
        last = messages[-1]
        role = get_role(last)
        if role == "assistant":
            if get_tool_calls(last):
                self.console.warn(
                    "The last assistant message has unresolved tool calls; "
                    "cannot continue safely."
                )
                return
            if get_text(last).strip():
                self.console.warn("The last round is already complete; nothing to continue.")
                return
            # An assistant turn with neither text nor tool calls left the task
            # mid-flight (reasoning-only round); resuming from it is exactly
            # what /continue is for.
        elif role == "tool":
            # All calls from the preceding assistant message must have results;
            # sending a partial tool batch is invalid for provider APIs.
            result_ids = set()
            index = len(messages) - 1
            while index >= 0 and get_role(messages[index]) == "tool":
                result_ids.add(get_tool_call_id(messages[index]))
                index -= 1
            calls = get_tool_calls(messages[index]) if index >= 0 else []
            call_ids = {call.get("id") for call in calls}
            if not call_ids or result_ids != call_ids:
                self.console.warn(
                    "The last assistant message has unresolved tool calls; "
                    "cannot continue safely."
                )
                return
        elif role != "user":
            self.console.warn("The current conversation state cannot be continued.")
            return

        self.console.rule()
        with self._round_timer():
            with self._operation("agent response"):
                self.get_response()
        try:
            self.save_session(self._session_id, reason="round")
        except Exception as e:
            self.console.warn(f"Could not save continued round: {e}")

    def _cmd_ps(self, raw: str) -> None:
        """List managed processes, or inspect one with a recent log tail."""
        parts = raw.split()
        if len(parts) > 2:
            self.console.warn("Usage: /ps [process-id]")
            return
        process_id = parts[1] if len(parts) == 2 else None
        result = self.tool_executor.inspect_processes(
            process_id=process_id,
            log_tail_chars=8000 if process_id else 0,
        )
        if not result["success"]:
            self.console.warn(result["error"])
            return
        processes = result["processes"]
        if not processes:
            self.console.system("No managed background processes.")
            return
        lines = []
        for process in processes:
            display = process.get("label") or process["command"]
            display = " ".join(display.split())
            if len(display) > 100:
                display = display[:97] + "..."
            status = process["status"]
            if status == "exited":
                status += f" ({process['exit_code']})"
            lines.append(
                f"  [cyan]{escape(process['process_id'])}[/cyan] "
                f"pid={process['pid']} [bold]{status}[/bold]  {escape(display)}"
            )
        self.console.print("[bold blue]Background processes:[/bold blue]\n" + "\n".join(lines))
        if process_id:
            process = processes[0]
            details = []
            if process.get("label"):
                details.append(f"  label: {escape(process['label'])}")
            details.extend((
                f"  command: {escape(process['command'])}",
                f"  cwd: {escape(process['cwd'])}",
                f"  log: {escape(process['log_path'])}",
            ))
            self.console.print("\n".join(details))
            tail = process.get("log_tail", "")
            if tail:
                prefix = "... (tail truncated)\n" if process.get("log_tail_truncated") else ""
                self.console.print(f"[bold blue]Recent output:[/bold blue]\n{escape(prefix + tail)}")
            else:
                self.console.print("[dim]No output captured yet.[/dim]")

    def _cmd_usage(self):
        ctx_tokens = self._context_tokens()
        ctx_pct = ctx_tokens / self.context_length * 100 if self.context_length else 0
        basis = "measured" if self.token_estimator.anchored else "estimated"

        self.console.print(
            f"[bold blue]Session usage (round {self.round_id}):[/bold blue]\n"
            f"  Total tokens   : {self.token_totals['total']}\n"
            f"  Prompt tokens  : {self.token_totals['prompt']}  (cached: {self.token_totals['cached_prompt']})\n"
            f"  Output tokens  : {self.token_totals['completion']}  (reasoning: {self.token_totals['reasoning']})\n"
            f"  Context window : ~{ctx_tokens} / {self.context_length} tokens [{ctx_pct:.0f}%, {basis}]\n"
            f"  Messages       : {len(self.context.messages)}\n"
            f"  Tool compaction: {self._tool_compaction_summary()}\n"
            f"  Compactions    : {self._compaction_summary()}"
        )

        skill_loads = self.tool_executor._skill_loads
        if skill_loads:
            summary = ", ".join(
                f"{n} ({c}\u00d7)"
                for n, c in sorted(skill_loads.items(), key=lambda kv: (-kv[1], kv[0]))
            )
            self.console.print(f"  Skills loaded  : {summary}")

    def _cmd_clear(self):
        self._restart_session()

    def _cmd_persona(self, raw: str = "/persona"):
        """List personas, or switch to one (switching restarts the conversation).

        Usage: ``/persona`` (list) | ``/persona reload`` | ``/persona <name>``.
        """
        parts = raw.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            self.console.print(f"[bold blue]Installed personas ({len(self.personas)}):[/bold blue]\n")
            for name, info in self.personas.items():
                current = " [green](current)[/green]" if name == self.persona.name else ""
                default = " [dim](default)[/dim]" if name == DEFAULT_PERSONA else ""
                tools = "all tools" if info.tools is None else (
                    f"tools: {', '.join(sorted(info.tools))}" if info.tools else "no tools"
                )
                bundled = "all bundled" if info.bundled_skills is None else (
                    f"bundled: {', '.join(sorted(info.bundled_skills))}"
                    if info.bundled_skills else "no bundled skills"
                )
                local = "local skills" if info.local_skills else "no local skills"
                self.console.print(f"  [cyan]{name}[/cyan]{default}{current}")
                if info.description:
                    self.console.print(f"    {info.description}")
                self.console.print(
                    f"    [dim]{tools} · {bundled} · {local} · {info.source} · {info.path}[/dim]"
                )
            self.console.print(
                "\n[dim]/persona <name> to switch · /persona reload to re-scan[/dim]"
            )
            return

        target = parts[1].strip()
        if target.lower() == "reload":
            self._reload_personas()
            return
        if target == self.persona.name:
            self.console.system(f"Already using persona '{target}'.")
            return
        try:
            persona = get_persona(target, personas=self.personas)
        except ValueError as e:
            self.console.error(str(e))
            return

        self._switch_persona(persona)

    def _report_persona_issues(self, issues: dict):
        for error in issues.get("errors", []):
            self.console.warn(
                f"persona '{error['name']}' ignored ({error['reason']}): {error['path']}"
            )
        shadowed = issues.get("shadowed", [])
        if shadowed:
            names = ", ".join(sorted({item["name"] for item in shadowed}))
            self.console.warn(f"lower-precedence duplicate persona(s) ignored: {names}")

    def _reload_personas(self):
        issues: dict = {}
        personas = discover_personas(self.work_dir, issues=issues)
        self._report_persona_issues(issues)
        if self.persona.name not in personas:
            self.console.error(
                f"Active persona '{self.persona.name}' is no longer available; reload aborted."
            )
            return

        previous = self.persona
        self.personas = personas
        current = personas[previous.name]
        if current.digest != previous.digest:
            self._switch_persona(current)
            self.console.system(f"Reloaded personas; active persona '{current.name}' changed.")
        else:
            self.persona = current
            self.console.system(f"Reloaded personas ({len(personas)} available).")

    def _switch_persona(self, persona: PersonaInfo):
        """Apply a new persona (prompt + tool surface) and restart the conversation.

        A persona switch is a full restart: the new prompt and tool whitelist
        would otherwise conflict with the existing history (old tool calls the
        new persona no longer offers, context sections it no longer shows).
        """
        # Save and clear while the old persona still owns the conversation.
        self._restart_session()
        self.persona = persona
        # self.tools follows the new persona automatically (live registry view).
        self.system_prompt = self._build_system_prompt()
        self.context.system_prompt["content"] = self.system_prompt
        self.console.system(f"Switched to persona: {persona.name}")


    def _cmd_system_prompt(self):
        self.console.print(
            f"[bold blue]System prompt:[/bold blue]\n\n{self.context.system_prompt['content']}"
        )

    def _cmd_context(self, raw: str = "/context"):
        msgs = self.context.messages
        parts = raw.split()
        if len(parts) > 2:
            self.console.warn("Usage: /context [id]")
            return

        if len(parts) == 2:
            message_id = parts[1].removeprefix("#")
            if not message_id.isdigit():
                self.console.warn("Usage: /context [id]")
                return
            idx = int(message_id)
            if idx >= len(msgs):
                self.console.warn(f"Context message #{idx} does not exist.")
                return
            self._print_context_message(idx, msgs[idx])
            return

        if not msgs:
            self.console.system("Context is empty (no messages).")
            return

        total_chars = 0
        lines = [f"[bold blue]Context log ({len(msgs)} messages):[/bold blue]"]

        tc_id_to_name = build_tool_name_index(msgs)

        for idx, m in enumerate(msgs):
            role = get_role(m)
            text = get_display_text(m) if role == "user" else get_text(m)
            chars = msg_chars(m)
            total_chars += chars

            preview = text.replace("\n", " ").strip()
            if len(preview) > 80:
                preview = preview[:77] + "..."

            role_tag = f"[cyan]{role:>9}[/cyan]"
            size_tag = f"[dim]{chars:>6} ch[/dim]"

            tcs = get_tool_calls(m) if role == "assistant" else []
            if tcs:
                n_tc = len(tcs)
                tc_names = ", ".join(tc.get("function", {}).get("name", "?") for tc in tcs)
                extra = f"[yellow]{n_tc} call{'s' if n_tc > 1 else ''}[/yellow] ({tc_names})"
                if preview:
                    lines.append(f"  [dim]#{idx:<3}[/dim] {role_tag} {size_tag}  {extra}  {preview}")
                else:
                    lines.append(f"  [dim]#{idx:<3}[/dim] {role_tag} {size_tag}  {extra}")
            elif role == "tool":
                tid = get_tool_call_id(m)
                tool_name = tc_id_to_name.get(tid, "?") if tid else "?"
                lines.append(f"  [dim]#{idx:<3}[/dim] {role_tag} {size_tag}  [magenta]({tool_name})[/magenta]  {preview}")
            else:
                lines.append(f"  [dim]#{idx:<3}[/dim] {role_tag} {size_tag}  {preview}")

        est_tokens = self.token_estimator.chars_to_tokens(total_chars)
        ctx_pct = est_tokens / self.context_length * 100 if self.context_length else 0
        lines.append(f"\n  [bold]Total:[/bold] ~{est_tokens:,} tokens / {self.context_length:,} [{ctx_pct:.0f}%]")

        lines.append("  [dim]Use /context <id> to show a message in full.[/dim]")
        self.console.print("\n".join(lines))

    def _print_context_message(self, idx: int, message: dict) -> None:
        """Print one context message without truncating or interpreting its text."""
        role = get_role(message)
        chars = msg_chars(message)
        self.console.print(
            f"[bold blue]Context message #{idx}[/bold blue] "
            f"([cyan]{escape(role)}[/cyan], {chars:,} ch)"
        )

        shown = False
        content = get_text(message)
        display_content = get_display_text(message)
        if display_content != content:
            self.console.print("[bold]Display content:[/bold]")
            self.console.print(display_content, markup=False)
            shown = True
        if content or "content" in message:
            self.console.print("[bold]Content:[/bold]")
            self.console.print(content, markup=False)
            shown = True

        reasoning = message.get("reasoning_content")
        if isinstance(reasoning, str):
            self.console.print("[bold]Reasoning:[/bold]")
            self.console.print(reasoning, markup=False)
            shown = True

        for call_idx, tool_call in enumerate(get_tool_calls(message)):
            function = tool_call.get("function", {})
            name = function.get("name", "?")
            call_id = tool_call.get("id")
            id_suffix = f" · {escape(str(call_id))}" if call_id else ""
            self.console.print(
                f"[bold]Tool call {call_idx}:[/bold] {escape(str(name))}{id_suffix}"
            )
            self.console.print(function.get("arguments") or "", markup=False)
            shown = True

        tool_call_id = get_tool_call_id(message)
        if tool_call_id:
            self.console.print(f"[bold]Tool call ID:[/bold] {escape(str(tool_call_id))}")
            shown = True

        if not shown:
            self.console.print("[dim](empty message)[/dim]")

    def _cmd_effort(self, raw: str):
        parts = raw.split(maxsplit=1)
        choices = ", ".join(REASONING_EFFORTS)
        if len(parts) == 1:
            self.console.system(
                f"Reasoning: {self.profile.reasoning or 'unsupported'}, effort: {self.reasoning_effort}. "
                f"Available: {choices}"
            )
            return
        effort = parts[1].strip().lower()
        if effort not in REASONING_EFFORTS:
            self.console.error(f"Invalid reasoning effort '{effort}'. Choose: {choices}")
            return
        self.reasoning_effort = effort
        self.console.system(f"Reasoning effort set to {effort}.")

    def _cmd_model(self, raw: str):
        from ene.config import conf

        parts = raw.split(maxsplit=1)
        openai_conf = conf.get("openai", {})

        if len(parts) < 2 or not parts[1].strip():
            lines = [
                f"[bold blue]Current model:[/bold blue] [cyan]{self.provider_name}/{self.model}[/cyan]"
                + (f" (alias: {self.model_alias})" if self.model_alias else "")
                + f" · {self.provider.auth_status()}",
                "[bold blue]Available models:[/bold blue]",
            ]
            for name, mc in openai_conf.items():
                marker = " [green]◀[/green]" if name == self.model_alias else ""
                provider = mc.get("provider", "openai")
                lines.append(
                    f"  [cyan]{name}[/cyan] → {provider}/{mc.get('model', name)}{marker}"
                )
            lines.append("\n  Usage: [cyan]/model <name>[/cyan]")
            self.console.print("\n".join(lines))
            return

        target = parts[1].strip()
        if target not in openai_conf:
            self.console.error(f"Model '{target}' not found in config. Use /model to list available models.")
            return

        if target == self.model_alias:
            self.console.system(f"Already using model '{target}'.")
            return

        model_conf = openai_conf[target]
        model = model_conf.get("model", target)
        profile = resolve_model_profile(model, target)
        provider_name = model_conf.get("provider", "openai")
        settings = ProviderSettings(
            api_key=model_conf.get("api_key", ""),
            base_url=model_conf.get("base_url", ""),
            reasoning_style=profile.reasoning,
        )
        try:
            provider = create_provider(provider_name, settings)
        except ValueError as e:
            self.console.error(str(e))
            return

        old_provider = self.provider
        self.model = model
        self.model_alias = target
        self.profile = profile
        self.provider_name = provider_name
        self._provider_settings = settings
        self.provider = provider
        old_provider.close()
        # self.tools reflects the new model's image support via the live property.
        self.context_length = model_conf.get("context_length", self.profile.context_length)
        self.max_output_tokens = model_conf.get("max_output_tokens", self.profile.max_output_tokens)
        self.reasoning_effort = model_conf.get("reasoning_effort", self.reasoning_effort)
        self.show_thinking = self.profile.reasoning is not None

        self.console.system(
            f"Switched to model: {self.model} via {self.provider_name} "
            f"(context: {self.context_length:,} tokens, reasoning: "
            f"{self.profile.reasoning or 'none'}/{self.reasoning_effort}, "
            f"auth: {self.provider.auth_status()})"
        )

    def _auth_provider(self, raw: str):
        """Resolve an auth command target to the active or a temporary provider."""
        from ene.config import conf

        parts = raw.split(maxsplit=1)
        target = parts[1].strip() if len(parts) > 1 else self.provider_name
        if target == self.provider_name or target == self.model_alias:
            return self.provider, self.provider_name, False

        model_conf = conf.get("openai", {}).get(target)
        if model_conf is not None:
            provider_name = model_conf.get("provider", "openai")
            model = model_conf.get("model", target)
            profile = resolve_model_profile(model, target)
            settings = ProviderSettings(
                api_key=model_conf.get("api_key", ""),
                base_url=model_conf.get("base_url", ""),
                reasoning_style=profile.reasoning,
            )
        else:
            provider_name = target
            settings = ProviderSettings()
        return create_provider(provider_name, settings), provider_name, True

    def _auth_interaction(self) -> AuthInteraction:
        return AuthInteraction(
            select=lambda message, choices: self.console.select(message, choices),
            prompt=lambda message: self.console.ask_text(message),
            notify=lambda message: self.console.print(message, markup=False),
            cancelled=lambda: bool(
                self.cancellation is not None and self.cancellation.cancelled
            ),
        )

    def _cmd_login(self, raw: str) -> None:
        provider = None
        temporary = False
        try:
            provider, provider_name, temporary = self._auth_provider(raw)
            with self._operation("authentication"):
                with self.console.thinking(
                    label="Authenticating",
                    progress=True,
                    status_suffix=provider_name,
                ):
                    provider.login(self._auth_interaction())
            self.console.system(
                f"Logged in to {provider_name}: {provider.auth_status()}"
            )
        except KeyboardInterrupt:
            self.console.system("Login cancelled.")
        except Exception as e:
            if getattr(e, "code", None) == "cancelled":
                self.console.system("Login cancelled.")
            else:
                self.console.error(f"Login failed: {e}")
        finally:
            if temporary and provider is not None:
                provider.close()

    def _cmd_logout(self, raw: str) -> None:
        provider = None
        temporary = False
        try:
            provider, provider_name, temporary = self._auth_provider(raw)
            provider.logout()
            self.console.system(f"Logged out of {provider_name}.")
        except Exception as e:
            self.console.error(f"Logout failed: {e}")
        finally:
            if temporary and provider is not None:
                provider.close()

    def _cmd_auth(self, raw: str) -> None:
        provider = None
        temporary = False
        try:
            provider, provider_name, temporary = self._auth_provider(raw)
            self.console.system(f"{provider_name}: {provider.auth_status()}")
        except Exception as e:
            self.console.error(f"Could not read authentication status: {e}")
        finally:
            if temporary and provider is not None:
                provider.close()

