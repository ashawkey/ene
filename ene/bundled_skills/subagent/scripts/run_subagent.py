#!/usr/bin/env python3
"""Run one independent ene agent, stream its log, and emit a JSON result."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from ene import run_agent
from ene.models import REASONING_EFFORTS
from ene.ui import AgentConsole
from ene.utils.io import AgentEvent, EventHub
from ene.utils.process import PROCESS_EVENTS_ENV


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    task = parser.add_mutually_exclusive_group(required=True)
    task.add_argument("--task", help="self-contained task for the subagent")
    task.add_argument("--task-file", type=Path, help="UTF-8 file containing the task")
    parser.add_argument("--model-alias", help="configured model alias")
    parser.add_argument("--persona", help="persona name (default: coder)")
    parser.add_argument("--work-dir", type=Path, help="subagent working directory")
    parser.add_argument("--reasoning-effort", choices=REASONING_EFFORTS)
    return parser.parse_args(argv)


def resolve_launch_options(
    args: argparse.Namespace, environ: dict[str, str] | None = None
) -> tuple[str | None, str | None]:
    """Resolve model alias and reasoning effort for the delegated run.

    Explicit CLI flags win. Otherwise inherit the parent session's identity
    from ``ENE_MODEL_ALIAS`` / ``ENE_REASONING_EFFORT`` (stamped into the
    environment of ``exec_command``/``start_process`` children by the agent's
    tool executor); when neither is present the run falls back to the first
    configured model, as ``run_agent`` does.
    """
    environ = os.environ if environ is None else environ
    model_alias = args.model_alias or environ.get("ENE_MODEL_ALIAS")
    reasoning_effort = args.reasoning_effort or environ.get("ENE_REASONING_EFFORT")
    return model_alias, reasoning_effort


RESULT_PREFIX = "ENE_SUBAGENT_RESULT="
# A single event line larger than this is replaced by a short notice, so a
# reader paging through the file always makes progress.
MAX_EVENT_LINE_BYTES = 192 * 1024


class EventLog:
    """Append the child agent's structured events to a JSON-lines file.

    A managed process exports the file path in ``ENE_PROCESS_EVENTS``; the Web
    UI reads it to show the delegated run as a conversation. Writing is
    best-effort: a failure disables the log and never affects the run.
    """

    def __init__(self, path: str):
        self._handle = open(path, "ab", buffering=0)
        self._echo_skipped = False

    def __call__(self, event: AgentEvent) -> None:
        if self._handle is None:
            return
        if (
            not self._echo_skipped
            and event.type == "system"
            and str(event.data.get("text", "")).startswith("Executing query: ")
        ):
            # The task already opens the log as a user message.
            self._echo_skipped = True
            return
        line = json.dumps(event.to_dict(), ensure_ascii=False).encode("utf-8")
        if len(line) > MAX_EVENT_LINE_BYTES:
            line = json.dumps({
                "seq": event.seq,
                "type": "debug",
                "data": {"text": f"({event.type} event too large to display)"},
                "timestamp": event.timestamp,
            }).encode("utf-8")
        try:
            self._handle.write(line + b"\n")
        except OSError:
            self.close()

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass


def open_event_log(environ: dict[str, str] | None = None) -> tuple[EventHub | None, EventLog | None]:
    """Return an event hub mirrored to ``ENE_PROCESS_EVENTS``, if requested.

    The variable is removed from this process's environment so commands the
    child agent runs cannot append to the same file.
    """
    environ = os.environ if environ is None else environ
    path = environ.pop(PROCESS_EVENTS_ENV, "")
    if not path:
        return None, None
    try:
        log = EventLog(path)
    except OSError:
        return None, None
    events = EventHub()
    events.add_listener(log)
    return events, log


def _emit(payload: dict, events: EventHub | None = None) -> None:
    """Emit the final machine-readable record after all live agent output."""
    if events is not None:
        # The response already ends the recorded conversation; leaving it out
        # keeps this record small enough to never be replaced as oversized.
        record = {key: value for key, value in payload.items() if key != "response"}
        if isinstance(record.get("error"), str):
            record["error"] = record["error"][:4000]
        events.publish("subagent_result", **record)
    print(f"{RESULT_PREFIX}{json.dumps(payload, ensure_ascii=False)}", flush=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    model_alias, reasoning_effort = resolve_launch_options(args)
    events, event_log = open_event_log()
    try:
        task = args.task
        if args.task_file is not None:
            task = args.task_file.read_text(encoding="utf-8")

        console = None
        if events is not None:
            console = AgentConsole(events)
            # Open the conversation with the delegated task itself.
            events.publish("user_message", text=task, source="task")
        result = run_agent(
            task,
            model_alias=model_alias,
            persona=args.persona,
            work_dir=args.work_dir,
            reasoning_effort=reasoning_effort,
            stream=True,
            quiet=False,
            console=console,
        )
        _emit({
            "success": result.success,
            "outcome": result.outcome.value,
            "response": result.response,
            "error": result.error,
            "token_usage": result.token_usage,
        }, events)
        if result.success:
            return 0
        if result.outcome.value == "user_interrupted":
            return 130
        return 1
    except KeyboardInterrupt:
        _emit({
            "success": False,
            "outcome": "user_interrupted",
            "response": None,
            "error": "Subagent interrupted.",
            "token_usage": {},
        }, events)
        return 130
    except Exception as exc:
        _emit({
            "success": False,
            "outcome": "failed",
            "response": None,
            "error": f"{type(exc).__name__}: {exc}",
            "token_usage": {},
        }, events)
        return 1
    finally:
        if event_log is not None:
            event_log.close()


if __name__ == "__main__":
    sys.exit(main())
