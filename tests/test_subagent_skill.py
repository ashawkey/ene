"""Tests for the bundled subagent skill runner."""

import json
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ene import TurnOutcome
from ene import skills as skills_module


_RUNNER = (
    Path(skills_module.BUNDLED_SKILLS_DIR) / "subagent" / "scripts" / "run_subagent.py"
)


def _load_runner():
    return runpy.run_path(str(_RUNNER), run_name="subagent_runner")


def _result_payload(output: str) -> dict:
    line = next(
        line for line in reversed(output.splitlines())
        if line.startswith("ENE_SUBAGENT_RESULT=")
    )
    return json.loads(line.removeprefix("ENE_SUBAGENT_RESULT="))


def test_subagent_runner_streams_output_and_emits_completed_result(
    monkeypatch, capsys, tmp_path
):
    runner = _load_runner()
    calls = []

    def fake_run_agent(task, **kwargs):
        calls.append((task, kwargs))
        print("live agent output")
        return SimpleNamespace(
            success=True,
            outcome=TurnOutcome.COMPLETED,
            response="done",
            error=None,
            token_usage={"total": 12},
        )

    monkeypatch.setitem(runner["main"].__globals__, "run_agent", fake_run_agent)

    code = runner["main"]([
        "--task", "inspect this", "--model-alias", "test", "--work-dir", str(tmp_path),
        "--reasoning-effort", "low",
    ])

    output = capsys.readouterr().out
    payload = _result_payload(output)
    assert code == 0
    assert "live agent output" in output
    assert output.splitlines()[-1].startswith("ENE_SUBAGENT_RESULT=")
    assert payload == {
        "success": True,
        "outcome": "completed",
        "response": "done",
        "error": None,
        "token_usage": {"total": 12},
    }
    assert calls == [("inspect this", {
        "model_alias": "test",
        "persona": None,
        "work_dir": Path(tmp_path),
        "reasoning_effort": "low",
        "stream": True,
        "quiet": False,
    })]


def test_subagent_runner_reads_task_file_and_reports_failure(monkeypatch, capsys, tmp_path):
    runner = _load_runner()
    task_file = tmp_path / "task.txt"
    task_file.write_text("task from file", encoding="utf-8")

    def fake_run_agent(task, **kwargs):
        assert task == "task from file"
        return SimpleNamespace(
            success=False,
            outcome=TurnOutcome.FAILED,
            response=None,
            error="request failed",
            token_usage={"total": 3},
        )

    monkeypatch.setitem(runner["main"].__globals__, "run_agent", fake_run_agent)

    code = runner["main"](["--task-file", str(task_file)])

    payload = _result_payload(capsys.readouterr().out)
    assert code == 1
    assert payload["outcome"] == "failed"
    assert payload["error"] == "request failed"


def test_subagent_runner_reports_startup_error(monkeypatch, capsys):
    runner = _load_runner()

    def fake_run_agent(task, **kwargs):
        raise ValueError("bad model")

    monkeypatch.setitem(runner["main"].__globals__, "run_agent", fake_run_agent)

    code = runner["main"](["--task", "inspect"])

    payload = _result_payload(capsys.readouterr().out)
    assert code == 1
    assert payload["outcome"] == "failed"
    assert payload["error"] == "ValueError: bad model"


def test_subagent_runner_cli_help():
    runner = _load_runner()
    with pytest.raises(SystemExit) as exc:
        runner["parse_args"](["--help"])
    assert exc.value.code == 0
