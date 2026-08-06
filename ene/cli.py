"""CLI entry point for ene: terminal-based AI agent.

Usage:
    ene [chat] [--model MODEL] [--verbose] [--resume [SESSION_ID]]
    ene {list,status,clean,hub,update,lib} ...
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from importlib.metadata import PackageNotFoundError, distribution
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from rich.table import Table

from ene.backend import LLMAgent
from ene.config import CONFIG_PATH, conf
from ene.hub import discover_hub
from ene.hubclient import HubClient
from ene.models import REASONING_EFFORTS, ReasoningEffort, resolve_model_profile
from ene.providers import provider_names
from ene.tools.process_manager import format_process_status
from ene.ui import AgentConsole
from ene.utils.io import CancellationToken, EventHub, InputBroker, PromptBroker


# ---------------------------------------------------------------------------
# CLI flags
# ---------------------------------------------------------------------------

@dataclass
class Args:
    """Terminal-based AI agent with tool-use, web access, and shell execution."""
    model: str = ""
    persona: str = ""  # persona to run as (see /persona; default: coder)
    verbose: bool = False
    stream: bool = True  # stream the response token-by-token as it is generated
    reasoning_effort: ReasoningEffort | None = None  # defaults to model config, then high

    resume: str | None = None  # --resume [session_id]
    web_port: int = 8765


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_agent(args: Args) -> "tuple[LLMAgent | None, HubClient | None]":
    """Create an LLMAgent (and its optional hub link) from parsed arguments.

    Returns ``(None, None)`` if the model is not found. If a reachable hub is
    running, the agent links to it automatically; otherwise it runs terminal-only.
    """
    console = AgentConsole()
    openai_conf = conf.get("openai", {})
    available_providers = provider_names()

    def model_config(alias: str, purpose: str = "") -> dict:
        label = f"{purpose.capitalize()} model" if purpose else "Model"
        model_conf = openai_conf.get(alias)
        if not isinstance(model_conf, dict):
            models = ", ".join(openai_conf)
            raise ValueError(
                f"{label} '{alias}' not found in config: {CONFIG_PATH}. "
                f"Available: {models}"
            )
        provider = model_conf.get("provider", "openai")
        if provider not in available_providers:
            available = ", ".join(available_providers)
            raise ValueError(
                f"Unknown {purpose + ' ' if purpose else ''}provider '{provider}'. "
                f"Available: {available}"
            )
        return model_conf

    try:
        if not args.model:
            if not openai_conf:
                raise ValueError(f"No models found in config: {CONFIG_PATH}")
            args.model = next(iter(openai_conf))

        model_conf = model_config(args.model)
        for purpose in ("recap", "summary"):
            config_key = f"{purpose}_model"
            alias = conf.get(config_key)
            if alias is None:
                continue
            if not isinstance(alias, str) or not alias.strip():
                raise ValueError(f"'{config_key}' must be a non-empty model alias")
            model_config(alias.strip(), purpose)
    except ValueError as e:
        console.error(str(e))
        return None, None

    provider_name = model_conf.get("provider", "openai")
    events = inputs = prompts = cancellation = hub_client = None

    info = discover_hub(args.web_port)
    if info:
        events = EventHub()
        inputs = InputBroker(events)
        prompts = PromptBroker(events)
        cancellation = CancellationToken(events, prompts)
        console = AgentConsole(events=events)

        cwd = os.getcwd()
        meta = {
            "title": f"{Path(cwd).name} · {args.model}",
            "cwd": cwd,
            "model": args.model,
            "provider": provider_name,
            "pid": os.getpid(),
            "host": socket.gethostname(),
        }
        hub_client = HubClient(
            events,
            inputs,
            prompts,
            cancellation,
            host=info.get("host", "127.0.0.1"),
            port=int(info.get("port", args.web_port)),
            token=info.get("token", ""),
            session_id=uuid.uuid4().hex,
            meta=meta,
        )

    try:
        agent = LLMAgent(
            model=model_conf.get("model", args.model),
            api_key=model_conf.get("api_key", ""),
            base_url=model_conf.get("base_url", ""),
            provider_name=provider_name,
            model_alias=args.model,
            verbose=args.verbose,
            stream=args.stream,
            reasoning_effort=args.reasoning_effort or model_conf.get("reasoning_effort", "high"),
            context_length=model_conf.get("context_length"),
            max_output_tokens=model_conf.get("max_output_tokens"),
            persona=args.persona,
            console=console,
            events=events,
            input_broker=inputs,
            prompt_broker=prompts,
            cancellation=cancellation,
        )
    except ValueError as e:
        console.error(f"Invalid provider configuration for '{args.model}': {e}")
        return None, None
    if hub_client is not None:
        hub_client.get_process_status = lambda: format_process_status(
            *agent.tool_executor.process_counts()
        )
    return agent, hub_client


def _last_user_preview(messages: list) -> str:
    """Extract a short preview from the last user message."""
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, list):
                text = " ".join(
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                )
            else:
                text = str(content)
            text = text.replace("\n", " ").strip()
            return text[:60] + ("..." if len(text) > 60 else "")
    return ""


def _pick_session(console: AgentConsole) -> str | None:
    """List saved sessions and let the user pick one interactively."""
    from ene.session_store import SessionStore
    from ene.utils import get_ene_dir

    sessions_dir = get_ene_dir() / "sessions"
    if not sessions_dir.exists():
        console.error(f"No sessions directory found: {sessions_dir}")
        return None

    files = sorted(
        (path for path in sessions_dir.iterdir() if path.is_dir() and (path / "history.jsonl").exists()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not files:
        console.system(f"No saved sessions in {sessions_dir}")
        return None

    entries: list[str] = []
    choice_labels: list[str] = []
    for f in files:
        stem = f.name
        try:
            meta = SessionStore(sessions_dir, stem).summary()
            messages = meta["messages"]
            n_msgs = meta["message_count"]
            rnd = meta["round_id"]
            model = meta["model"]
            preview = _last_user_preview(messages)
        except Exception:
            n_msgs, rnd, model, preview = "?", "?", "?", ""
        entries.append(stem)
        label = f"{stem}  │  msgs:{n_msgs}  rounds:{rnd}  model:{model}"
        if preview:
            label += f"  │  {preview}"
        choice_labels.append(label)

    picked = console.select(
        message="Pick a session to resume",
        choices=choice_labels,
    )
    if picked is None:
        return None

    for i, label in enumerate(choice_labels):
        if label == picked:
            return entries[i]

    console.error("Invalid selection.")
    return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list():
    console = AgentConsole()
    openai_conf = conf.get("openai", {})

    if not openai_conf:
        exists = CONFIG_PATH.exists()
        console.error(f"No models found in config: {CONFIG_PATH}")
        if not exists:
            console.print(
                f"Config file does not exist. Create it at:\n  {CONFIG_PATH}\n\n"
                "Example:\n"
                "openai:\n"
                "  my-model:\n"
                "    model: gpt-4o\n"
                "    api_key: sk-...\n"
                "    base_url: https://api.openai.com/v1"
            )
        else:
            console.print("Please add model configurations under the 'openai' key.")
        return

    table = Table(title="Available Models", show_header=True, header_style="bold magenta")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Model", style="green")
    table.add_column("Provider", style="magenta")
    table.add_column("Base URL", style="yellow")
    table.add_column("Context", style="blue", justify="right")
    table.add_column("Thinking", style="magenta")

    for name, model_conf in openai_conf.items():
        model_id = model_conf.get("model", name)
        profile = resolve_model_profile(model_id, name)
        context_length = model_conf.get("context_length", profile.context_length)
        ctx = f"{context_length // 1000}K"
        table.add_row(
            name,
            model_id,
            model_conf.get("provider", "openai"),
            model_conf.get("base_url", "N/A"),
            ctx,
            f"{profile.reasoning or '-'} / {model_conf.get('reasoning_effort', 'high')}",
        )

    console.table(table)


def cmd_storage():
    """Show allocated disk usage for each entry in the project .ene directory."""
    from ene.utils.storage import (
        PRESERVED_ENTRIES,
        format_size,
        ene_storage_dir,
        storage_entries,
    )

    console = AgentConsole()
    root = ene_storage_dir()
    entries = storage_entries()
    if not entries:
        console.system(f"No storage found in {root}")
        return

    table = Table(title=f"ene storage: {root}")
    table.add_column("Entry", style="cyan")
    table.add_column("Type", style="dim")
    table.add_column("Size", justify="right", style="green")
    table.add_column("Default clean", style="dim")
    for entry in entries:
        table.add_row(
            entry.name,
            "directory" if entry.is_dir else "file",
            format_size(entry.size),
            "no" if entry.name in PRESERVED_ENTRIES else "yes",
        )
    table.add_section()
    table.add_row("Total", "", format_size(sum(entry.size for entry in entries)), "", style="bold")
    console.table(table)


def cmd_clean(names: list[str] | None = None, *, history: bool = False):
    """Remove disposable storage, selected entries, or conversation history."""
    from ene.utils.storage import (
        clean_storage,
        cleanable_entries,
        format_size,
        ene_storage_dir,
        storage_entries,
    )

    console = AgentConsole()
    available = {entry.name: entry for entry in storage_entries()}
    if names:
        unknown = sorted(set(names) - available.keys())
        if unknown:
            console.error(f"Storage entry not found: {', '.join(unknown)}")
            if available:
                console.print(f"Available entries: {', '.join(available)}")
            return
        entries = [available[name] for name in dict.fromkeys(names)]
    else:
        entries = cleanable_entries()

    sessions = available.get("sessions")
    if history and sessions is not None and sessions not in entries:
        entries.append(sessions)

    if not entries:
        console.system(f"Nothing to clean in {ene_storage_dir()}")
        return

    removed = clean_storage(entries=entries)
    cleaned = ", ".join(entry.name for entry in entries)
    console.system(f"Cleaned {format_size(removed)} ({cleaned}) from {ene_storage_dir()}")


def _editable_source() -> Path | None:
    """Return the source directory for a PEP 610 editable install."""
    try:
        direct_url_text = distribution("ene-agent").read_text("direct_url.json")
    except PackageNotFoundError:
        return None
    if not direct_url_text:
        return None

    direct_url = json.loads(direct_url_text)
    if not direct_url.get("dir_info", {}).get("editable", False):
        return None

    parsed = urlparse(direct_url["url"])
    if parsed.scheme != "file":
        raise RuntimeError(f"Unsupported editable install URL: {direct_url['url']}")
    return Path(url2pathname(parsed.path))


def cmd_update() -> int:
    console = AgentConsole()
    source = _editable_source()

    if source is not None:
        console.system(f"Updating editable install in {source}")
        command = ["git", "-C", str(source), "pull"]
        action = "git pull"
        commands = [command]
    else:
        console.system("Replacing installed ene with the latest source")
        action = "ene update"
        commands = [
            [sys.executable, "-m", "pip", "uninstall", "-y", "ene-agent"],
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "ene-agent @ git+https://github.com/ashawkey/ene.git",
            ],
        ]

    for command in commands:
        result = subprocess.run(command)
        if result.returncode != 0:
            console.error(f"{action} failed (exit code {result.returncode}).")
            return result.returncode

    console.system("ene updated successfully.")
    return 0


def cmd_hub(args: Args):
    """Run the shared web hub daemon (owns the public port)."""
    console = AgentConsole()
    from ene.hub import Hub

    try:
        hub = Hub(port=args.web_port, token=conf.get("ene_web_token"), console=console)
        hub.start()
    except Exception as exc:
        console.error(f"Could not start hub: {exc}")
        return

    console.system(f"ene hub running at {hub.url}")
    console.local(f"[bold yellow]Web access token:[/bold yellow] {hub.token}")
    console.system("Agents started with `ene` will auto-link while this hub is running.")
    console.system("Press Ctrl+C to stop the hub.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        hub.stop()
        console.system("Hub stopped.")


def cmd_chat(args: Args):
    agent, hub_client = get_agent(args)
    if not agent:
        return

    if hub_client is not None:
        hub_client.start()
        agent.console.system(
            f"Linked to ene hub at {hub_client.host}:{hub_client.port} "
            f"(session {hub_client.session_id[:8]})"
        )

    try:
        # Handle --resume
        session_id: str | None = args.resume
        if session_id == "":  # bare --resume → pick interactively
            session_id = _pick_session(agent.console)

        if session_id:
            agent.load_session(session_id)

        agent.chat_loop(resumed_session_id=session_id)
    finally:
        if hub_client is not None:
            hub_client.stop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _add_chat_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", default="", help="model alias from ~/.ene.yaml")
    parser.add_argument("--persona", default="", help="persona to run as")
    parser.add_argument("--verbose", action="store_true", help="show detailed output")
    stream = parser.add_mutually_exclusive_group()
    stream.add_argument("--stream", dest="stream", action="store_true", help="stream responses (default)")
    stream.add_argument("--no-stream", dest="stream", action="store_false", help="do not stream responses")
    parser.set_defaults(stream=True)
    parser.add_argument("--reasoning-effort", choices=REASONING_EFFORTS)
    parser.add_argument(
        "--resume", nargs="?", const="", default=None, metavar="SESSION_ID",
        help="resume a session; omit SESSION_ID to choose interactively",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ene", description="Terminal AI agent with an optional synchronized Web UI"
    )
    commands = parser.add_subparsers(dest="command")

    chat = commands.add_parser("chat", help="start an interactive chat")
    _add_chat_options(chat)
    commands.add_parser("list", help="list configured models")
    commands.add_parser("status", help="show project .ene storage usage")
    clean = commands.add_parser("clean", help="clean disposable project .ene storage")
    clean.add_argument("entries", nargs="*", metavar="ENTRY")
    clean.add_argument(
        "--history",
        action="store_true",
        help="also remove saved conversation sessions",
    )
    hub = commands.add_parser("hub", help="run the shared web hub")
    hub.add_argument("--web-port", type=int, default=8765, help="listener port (default: 8765)")
    commands.add_parser("update", help="update ene from its source repository")
    commands.add_parser("lib", help="manage the Git-backed resource library", add_help=False)
    return parser


def _implicit_chat(raw_args: list[str]) -> list[str]:
    """Insert the default ``chat`` command for legacy flag-only invocation."""
    if not raw_args:
        return ["chat"]
    if raw_args[0] in {"chat", "list", "status", "clean", "hub", "update", "lib"}:
        return raw_args
    if raw_args[0] in {"-h", "--help"}:
        return raw_args
    return ["chat", *raw_args]


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args and raw_args[0] == "lib":
        from ene import library_cli
        return library_cli.main(raw_args[1:])
    args = build_parser().parse_args(_implicit_chat(raw_args))

    if args.command == "list":
        cmd_list()
    elif args.command == "status":
        cmd_storage()
    elif args.command == "clean":
        cmd_clean(args.entries, history=args.history)
    elif args.command == "hub":
        cmd_hub(Args(web_port=args.web_port))
    elif args.command == "update":
        return cmd_update()
    elif args.command == "chat":
        cmd_chat(Args(
            model=args.model,
            persona=args.persona,
            verbose=args.verbose,
            stream=args.stream,
            reasoning_effort=args.reasoning_effort,
            resume=args.resume,
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
