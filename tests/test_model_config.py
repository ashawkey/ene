"""Temporary endpoints and explicit model capabilities at session startup."""

from copy import deepcopy

import pytest

from ene import config
from ene.backend import LLMAgent
from ene.model_config import resolve_session_model
from ene.models import resolve_model_profile
from ene.providers import CompletionRequest


TEMPORARY = {"base_url": "http://localhost:8000/v1", "model": "qwen-sft"}


def test_temporary_endpoint_works_without_config_or_inherited_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-use")
    configured = {"openai": {"qwen-sft": {
        "model": "other-model", "api_key": "private", "provider": "openai-codex",
        "base_url": "https://other.example/v1", "context_length": 999,
    }}}
    original = deepcopy(configured)
    resolved = resolve_session_model(TEMPORARY, configured)
    assert resolved == resolve_session_model(TEMPORARY, {})
    assert configured == original
    assert resolved["model"] == "qwen-sft"
    assert resolved["model_alias"] == ""
    assert resolved["api_key"] == "EMPTY"
    assert resolved["provider_name"] == "openai"
    assert resolved["api"] == "chat_completions"
    assert resolved["context_length"] is None


def test_temporary_credentials_and_api(monkeypatch):
    monkeypatch.setenv("VLLM_KEY", "temporary-secret")
    for credentials in ({"api_key": "temporary-secret"}, {"api_key_env": "VLLM_KEY"}):
        resolved = resolve_session_model({**TEMPORARY, **credentials, "api": "responses"}, {})
        assert resolved["api_key"] == "temporary-secret"
        assert resolved["api"] == "responses"


@pytest.mark.parametrize("extra, error", [
    ({"model": ""}, "requires --model"),
    ({"base_url": ""}, "HTTP"),
    ({"base_url": "localhost:8000"}, "HTTP"),
    ({"base_url": "ftp://localhost/v1"}, "HTTP"),
    ({"base_url": "http://secret@localhost/v1"}, "must not contain credentials"),
    ({"api_key": "secret", "api_key_env": "KEY"}, "mutually exclusive"),
    ({"api": "invalid"}, "api must be"),
    ({"model_profile": "qwen3.8-27"}, "Unknown model profile"),
    ({"model_profile": "QWEN3.8-27B"}, "Unknown model profile"),
    ({"context_length": 0}, "positive integer"),
    ({"max_output_tokens": -1}, "positive integer"),
])
def test_invalid_temporary_options(extra, error):
    with pytest.raises(ValueError, match=error):
        resolve_session_model({**TEMPORARY, **extra}, {})


def test_missing_environment_key_is_reported(monkeypatch):
    monkeypatch.delenv("VLLM_KEY", raising=False)
    with pytest.raises(ValueError, match="unset or empty"):
        resolve_session_model({**TEMPORARY, "api_key_env": "VLLM_KEY"}, {})


@pytest.mark.parametrize("option, value", [("api_key", "key"), ("api_key_env", "KEY"), ("api", "responses")])
def test_connection_overrides_require_explicit_endpoint(option, value):
    with pytest.raises(ValueError, match="requires --base-url"):
        resolve_session_model({"model": "configured", option: value}, {"openai": {"configured": {}}})


def test_profile_and_budget_overrides_work_with_configured_aliases():
    configured = {"openai": {"local": {
        "model": "qwen-sft", "context_length": 32768, "max_output_tokens": 8192,
    }}}
    resolved = resolve_session_model({"model": "loc", "model_profile": "qwen3.8-27b",
                                      "max_output_tokens": 4096}, configured)
    assert resolved["model_alias"] == "local"
    assert resolved["model"] == "qwen-sft"
    assert resolved["model_profile"] == "qwen3.8-27b"
    assert resolved["context_length"] == 32768
    assert resolved["max_output_tokens"] == 4096


@pytest.mark.parametrize("budgets", [{}, {"context_length": 32768, "max_output_tokens": 4096}])
def test_agent_profile_override_preserves_wire_model_and_resets_on_switch(monkeypatch, tmp_path, budgets):
    monkeypatch.setattr(config, "conf", {"openai": {"text": {"model": "custom-text"}}})
    options = resolve_session_model({**TEMPORARY, "model_profile": "qwen3.8-27b", **budgets}, {})
    agent = LLMAgent(**options, work_dir=str(tmp_path), terminal_prompts=False)
    try:
        assert agent.model == "qwen-sft"
        assert agent.profile == resolve_model_profile("qwen3.8-27b")
        assert agent.profile.reasoning == "qwen3.8"
        assert agent.tool_executor.supports_image_input is True
        assert agent.context_length == budgets.get("context_length", 262144)
        assert agent.max_output_tokens == budgets.get("max_output_tokens", 64000)
        request = CompletionRequest(model=agent.model, messages=[], reasoning_effort="none",
                                    max_output_tokens=agent.max_output_tokens)
        wire = agent.provider._kwargs(request)
        assert wire["model"] == "qwen-sft"
        assert wire["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
        assert wire["max_tokens"] == agent.max_output_tokens
        saved = agent._session_data()
        assert not {"api_key", "base_url", "model_profile"} & saved.keys()
        agent._cmd_model("/model text")
        assert agent.model == "custom-text"
        assert agent.profile == resolve_model_profile("custom-text")
        assert agent.tool_executor.supports_image_input is False
        assert agent.context_length == 128000
        assert agent.max_output_tokens == 32000
    finally:
        agent.close()
