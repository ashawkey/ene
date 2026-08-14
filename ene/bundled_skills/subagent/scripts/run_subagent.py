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


def _emit(payload: dict) -> None:
    """Emit the final machine-readable record after all live agent output."""
    print(f"{RESULT_PREFIX}{json.dumps(payload, ensure_ascii=False)}", flush=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    model_alias, reasoning_effort = resolve_launch_options(args)
    try:
        task = args.task
        if args.task_file is not None:
            task = args.task_file.read_text(encoding="utf-8")

        result = run_agent(
            task,
            model_alias=model_alias,
            persona=args.persona,
            work_dir=args.work_dir,
            reasoning_effort=reasoning_effort,
            stream=True,
            quiet=False,
        )
        _emit({
            "success": result.success,
            "outcome": result.outcome.value,
            "response": result.response,
            "error": result.error,
            "token_usage": result.token_usage,
        })
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
        })
        return 130
    except Exception as exc:
        _emit({
            "success": False,
            "outcome": "failed",
            "response": None,
            "error": f"{type(exc).__name__}: {exc}",
            "token_usage": {},
        })
        return 1


if __name__ == "__main__":
    sys.exit(main())
