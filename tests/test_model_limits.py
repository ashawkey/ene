"""Serving-limit discovery without a live model endpoint."""

from concurrent.futures import ThreadPoolExecutor
import httpx
import pytest
from openai import OpenAI

import ene.config as config
from ene.backend import LLMAgent
from ene.messages import Message
from ene.providers import CompletionResult, OpenAICompatibleProvider, ProviderSettings
from ene.ui import AgentConsole


@pytest.mark.parametrize("api", ["chat_completions", "responses"])
@pytest.mark.parametrize("limit", [32768, None, "32768", True, 0, -1, 1.5])
def test_models_metadata_requires_exact_id_and_positive_integer(monkeypatch, api, limit):
    from ene.providers import create_provider

    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"object": "list", "data": [
            {"id": "other", "object": "model", "created": 0,
             "owned_by": "vllm", "max_model_len": 999999},
            {"id": "b0-step11280", "object": "model", "created": 0,
             "owned_by": "vllm", "max_model_len": limit},
        ]})

    provider = create_provider("openai", ProviderSettings(api=api))
    client = OpenAI(api_key="test-key", base_url="http://server/v1",
                    max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(provider, "_new_client", lambda: client)
    assert provider.context_length("b0-step11280") == (32768 if type(limit) is int and limit > 0 else None)
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/models"
    assert requests[0].headers["authorization"] == "Bearer test-key"
    assert requests[0].extensions["timeout"]["read"] == 5
    assert client.is_closed
    assert provider._active_client is None


@pytest.mark.parametrize("status", [401, 404, 500])
def test_unavailable_models_endpoint_does_not_block_completions(monkeypatch, status):
    provider = OpenAICompatibleProvider(ProviderSettings())
    client = OpenAI(api_key="test-key", base_url="http://server/v1", max_retries=0,
                    http_client=httpx.Client(transport=httpx.MockTransport(
                        lambda request: httpx.Response(status, json={"error": {"message": "unavailable"}})
                    )))
    monkeypatch.setattr(provider, "_new_client", lambda: client)
    assert provider.context_length("model") is None
    assert client.is_closed


@pytest.mark.parametrize("data", [[], None, [{"id": "other", "max_model_len": 32768}]])
def test_missing_model_metadata_is_not_inferred_from_other_entries(monkeypatch, data):
    provider = OpenAICompatibleProvider(ProviderSettings())
    client = OpenAI(api_key="test-key", base_url="http://server/v1", max_retries=0,
                    http_client=httpx.Client(transport=httpx.MockTransport(
                        lambda request: httpx.Response(200, json={"object": "list", "data": data})
                    )))
    monkeypatch.setattr(provider, "_new_client", lambda: client)
    assert provider.context_length("b0-step11280") is None
    assert client.is_closed


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError])
def test_discovery_transport_failure_falls_back_without_retry(monkeypatch, error_type):
    requests = []

    def handle(request):
        requests.append(request)
        raise error_type("offline", request=request)

    provider = OpenAICompatibleProvider(ProviderSettings())
    client = OpenAI(api_key="test-key", base_url="http://server/v1", max_retries=0,
                    http_client=httpx.Client(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(provider, "_new_client", lambda: client)
    assert provider.context_length("model") is None
    assert len(requests) == 1
    assert client.is_closed


@pytest.fixture
def agent_factory(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "conf", {})
    agents = []

    def make(limit=32768, **overrides):
        queries = []
        monkeypatch.setattr(OpenAICompatibleProvider, "context_length",
                            lambda provider, model: queries.append(model) or limit)
        agent = LLMAgent(
            model="b0-step11280", model_profile="qwen3-vl-30b-a3b",
            api_key="test-key", base_url="http://server/v1", stream=False,
            terminal_prompts=False, console=AgentConsole(render_terminal=False),
            work_dir=str(tmp_path), **overrides,
        )
        agent.context.add(Message.user("hello"))
        requests = []

        def complete(request):
            requests.append(request)
            return CompletionResult(Message.assistant("done"), None, "stop")

        agent._blocking_completion = complete
        agents.append(agent)
        return agent, queries, requests

    yield make
    for agent in agents:
        agent.close()


def test_detects_serving_limit_once_and_adjusts_output_budget(agent_factory):
    agent, queries, requests = agent_factory()
    assert queries == []  # No network work during construction.
    agent.call_api()
    agent.call_api()
    assert queries == ["b0-step11280"]
    assert agent.context_length == 32768
    assert agent.max_output_tokens == 8192
    assert [request.max_output_tokens for request in requests] == [8192, 8192]
    assert agent.profile.context_length == 262144


def test_missing_metadata_keeps_profile_defaults_and_is_not_requeried(agent_factory):
    agent, queries, requests = agent_factory(limit=None)
    agent.call_api()
    agent.call_api()
    assert len(queries) == 1
    assert agent.context_length == 262144
    assert requests[0].max_output_tokens == 32000


def test_context_override_skips_discovery_and_derives_output_budget(agent_factory):
    agent, queries, requests = agent_factory(context_length=16384)
    agent.call_api()
    assert queries == []
    assert agent.context_length == 16384
    assert requests[0].max_output_tokens == 4096


def test_output_override_is_preserved_with_detected_context(agent_factory):
    agent, queries, requests = agent_factory(max_output_tokens=12000)
    agent.call_api()
    assert len(queries) == 1
    assert agent.context_length == 32768
    assert requests[0].max_output_tokens == 12000


def test_discovery_is_shared_between_parallel_batch_items(agent_factory, monkeypatch):
    agent, queries, requests = agent_factory()
    class BatchProvider:
        def complete(self, request):
            requests.append(request)
            return CompletionResult(Message.assistant("done"), None, "stop")

        def close(self):
            pass

    monkeypatch.setattr("ene.backend.batch.create_provider", lambda *args: BatchProvider())
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda item: agent.run_batch_completion("Do", item), ["a", "b", "c"]))
    assert len(results) == 3
    assert queries == ["b0-step11280"]
    assert all(request.max_output_tokens == 8192 for request in requests)


def test_model_switch_resets_discovery(agent_factory, monkeypatch):
    agent, queries, requests = agent_factory()
    agent.call_api()
    monkeypatch.setattr(config, "conf", {"openai": {"next": {
        "model": "other-checkpoint", "api_key": "test-key", "base_url": "http://server/v1",
    }}})
    agent._cmd_model("/model next")
    agent.call_api()
    assert queries == ["b0-step11280", "other-checkpoint"]
    assert agent.context_length == 32768
    assert requests[-1].max_output_tokens == 8192


class _OverflowError(Exception):
    status_code = 400


@pytest.mark.parametrize("reported", ["32768", "32,768"])
def test_overflow_learns_smaller_limit_and_retries_without_compacting(agent_factory, reported):
    agent, queries, requests = agent_factory(limit=None)
    compactions = []
    agent._run_compaction = lambda reason: compactions.append(reason) or False

    def complete(request):
        requests.append(request)
        if len(requests) == 1:
            raise _OverflowError(
                f"This model's maximum context length is {reported} tokens. "
                "However, you requested 32000 output tokens and your prompt "
                "contains at least 769 input tokens."
            )
        return CompletionResult(Message.assistant("done"), None, "stop")

    agent._blocking_completion = complete
    assert agent.call_api().text == "done"
    assert [request.max_output_tokens for request in requests] == [32000, 8192]
    assert agent.context_length == 32768
    assert compactions == []
    agent.call_api()
    assert len(queries) == 1
    assert requests[-1].max_output_tokens == 8192


def test_error_limit_recovery_is_bounded(agent_factory):
    agent, queries, requests = agent_factory(limit=None)
    agent._run_compaction = lambda reason: False

    def complete(request):
        requests.append(request)
        raise _OverflowError("maximum context length is 32768 tokens")

    agent._blocking_completion = complete
    with pytest.raises(RuntimeError, match="API request rejected"):
        agent.call_api()
    assert len(requests) == 2


def test_error_does_not_replace_explicit_context(agent_factory):
    agent, queries, requests = agent_factory(context_length=64000)
    agent._run_compaction = lambda reason: False
    def complete(request):
        raise _OverflowError("maximum context length is 32768 tokens")

    agent._blocking_completion = complete
    with pytest.raises(RuntimeError, match="API request rejected"):
        agent.call_api()
    assert queries == []
    assert agent.context_length == 64000
