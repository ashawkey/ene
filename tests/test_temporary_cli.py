"""CLI and detached-worker handoff for temporary connections."""

import json
import os
from types import SimpleNamespace

import pytest

from ene import cli, live, live_worker


@pytest.mark.parametrize("invocation", [[], ["new", "temporary"], ["resume", "saved"]])
def test_temporary_flags_reach_chat(monkeypatch, invocation):
    calls = []
    monkeypatch.setattr(cli, "cmd_chat", calls.append)
    cli.main([*invocation, "--base-url", "http://localhost:8000/v1", "--model", "qwen-sft",
              "--model-profile", "qwen3.8-27b", "--context-length", "32768",
              "--max-output-tokens", "4096", "--api-key-env", "VLLM_KEY", "--api", "responses"])
    args = calls[0]
    assert args.model == "qwen-sft"
    assert args.base_url == "http://localhost:8000/v1"
    assert args.model_profile == "qwen3.8-27b"
    assert args.context_length == 32768
    assert args.max_output_tokens == 4096
    assert args.api_key_env == "VLLM_KEY"
    assert args.api == "responses"
    assert args.resume == ("saved" if invocation and invocation[0] == "resume" else None)


@pytest.mark.parametrize("flags", [
    ["--model-profile", "unknown"],
    ["--api", "unknown"],
    ["--api-key", "secret", "--api-key-env", "KEY"],
])
def test_invalid_flag_choices_fail_in_parser(flags):
    with pytest.raises(SystemExit) as error:
        cli.main(flags)
    assert error.value.code == 2


def test_chat_resolves_env_key_without_config(monkeypatch):
    monkeypatch.setattr(cli, "conf", {})
    monkeypatch.setenv("VLLM_KEY", "temporary-secret")
    calls, attached = [], []
    monkeypatch.setattr(live, "start_session", lambda **kwargs: calls.append(kwargs) or {"runtime_id": "test"})
    monkeypatch.setattr(cli, "_attach_live", attached.append)
    cli.cmd_chat(cli.Args(model="qwen-sft", base_url="http://localhost:8000/v1",
                          api_key_env="VLLM_KEY", model_profile="qwen3.8-27b"))
    options = calls[0]["options"]
    assert options["api_key"] == "temporary-secret"
    assert "api_key_env" not in options
    assert options["model_profile"] == "qwen3.8-27b"
    assert attached == [{"runtime_id": "test"}]


def test_invalid_connection_fails_before_worker_spawn(monkeypatch):
    errors = []
    monkeypatch.setattr(cli, "conf", {})
    monkeypatch.setattr(cli, "AgentConsole", lambda: SimpleNamespace(error=errors.append))
    monkeypatch.setattr(live, "start_session", lambda **kwargs: pytest.fail("spawned worker"))
    cli.cmd_chat(cli.Args(base_url="http://localhost:8000/v1"))
    assert "requires --model" in errors[0]


def test_get_agent_uses_temporary_profile(monkeypatch):
    monkeypatch.setattr(cli, "conf", {})
    monkeypatch.setattr(cli, "LLMAgent", lambda **kwargs: kwargs)
    agent = cli.get_agent(cli.Args(model="qwen-sft", base_url="http://localhost:8000/v1",
                                   model_profile="qwen3.8-27b"))
    assert agent["model"] == "qwen-sft"
    assert agent["model_alias"] == ""
    assert agent["model_profile"] == "qwen3.8-27b"


def test_start_session_keeps_secret_out_of_record(monkeypatch, tmp_path):
    monkeypatch.setattr(live, "LIVE_DIR", tmp_path / "live")
    monkeypatch.setattr(live, "REGISTRY_LOCK", tmp_path / "live" / ".lock")
    launched = []
    monkeypatch.setattr(live, "launch_worker", lambda record, **kwargs: launched.append(kwargs) or record)
    options = {"base_url": "http://localhost:8000/v1", "model": "qwen-sft", "api_key": "temporary-secret"}
    record = live.start_session(name="", workspace=str(tmp_path), options=options)
    assert launched == [{"api_key": "temporary-secret"}]
    assert "api_key" not in record["options"]
    assert "temporary-secret" not in live.record_path(record["runtime_id"]).read_text()
    assert options["api_key"] == "temporary-secret"  # No mutation of the caller's options.


def test_launch_worker_uses_private_environment_not_argv_or_files(monkeypatch, tmp_path):
    monkeypatch.setattr(live, "LIVE_DIR", tmp_path / "live")
    monkeypatch.setattr(live, "REGISTRY_LOCK", tmp_path / "live" / ".lock")
    monkeypatch.setenv(live.WORKER_API_KEY_ENV, "stale-parent-value")
    record = live.create_record(name="", workspace=str(tmp_path), options={})
    launched = []
    monkeypatch.setattr(live.subprocess, "Popen", lambda argv, **kwargs: launched.append((argv, kwargs)))
    monkeypatch.setattr(live, "read_record", lambda path: {**record, "status": "ready"})
    monkeypatch.setattr(live, "probe", lambda record: {})
    live.launch_worker(record, api_key="temporary-secret")
    argv, kwargs = launched[0]
    assert kwargs["env"][live.WORKER_API_KEY_ENV] == "temporary-secret"
    assert "temporary-secret" not in json.dumps(argv)
    assert "temporary-secret" not in live.record_path(record["runtime_id"]).read_text()
    assert not live.record_path(record["runtime_id"]).with_suffix(".log").read_text()
    assert os.environ[live.WORKER_API_KEY_ENV] == "stale-parent-value"
    live.launch_worker(record)
    assert live.WORKER_API_KEY_ENV not in launched[1][1]["env"]


def test_worker_consumes_secret_and_forwards_temporary_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(live_worker, "conf", {})
    monkeypatch.setenv(live.WORKER_API_KEY_ENV, "temporary-secret")
    monkeypatch.setattr(live_worker, "LLMAgent", lambda **kwargs: kwargs)
    options = {"base_url": "http://localhost:8000/v1", "model": "qwen-sft",
               "model_profile": "qwen3.8-27b", "context_length": 32768, "max_output_tokens": 4096}
    worker = live_worker.Worker({"runtime_id": "test", "token": "test",
                                "workspace": str(tmp_path), "options": options})
    agent = worker._make_agent()
    assert agent["api_key"] == "temporary-secret"
    assert agent["model"] == "qwen-sft"
    assert agent["model_alias"] == ""
    assert agent["model_profile"] == "qwen3.8-27b"
    assert agent["context_length"] == 32768
    assert agent["max_output_tokens"] == 4096
    assert live.WORKER_API_KEY_ENV not in os.environ
    assert "api_key" not in options


def test_new_session_does_not_inherit_temporary_backend(monkeypatch, tmp_path):
    calls = []
    record = {"runtime_id": "old", "workspace": str(tmp_path), "options": {
        "model": "qwen-sft", "base_url": "http://localhost:8000/v1", "api": "responses",
        "model_profile": "qwen3.8-27b", "context_length": 32768,
        "max_output_tokens": 4096, "persona": "coder", "resume": "old",
    }}

    class Terminal:
        def __init__(self, record, *, new_session, **kwargs):
            self.new_session = new_session

        def run(self):
            self.new_session("fresh")
            return "detach", ""

    monkeypatch.setattr("ene.live_terminal.LiveTerminal", Terminal)
    monkeypatch.setattr(live, "start_session", lambda **kwargs: calls.append(kwargs))
    cli._attach_live(record)
    assert calls[0]["options"] == {"persona": "coder", "resume": None}
    assert record["options"]["model"] == "qwen-sft"
