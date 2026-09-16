"""API-key Responses transport, wire formats, and state replay."""

import json
from dataclasses import replace

import httpx
import pytest
from openai import OpenAI

from ene.messages import ImagePart, Message, TextPart
from ene.providers import (
    CompletionRequest, OpenAICompatibleProvider, OpenAIResponsesProvider,
    ProviderError, ProviderSettings, create_provider,
)
from ene.providers.openai_codex import _build_body
from ene.utils.interrupt import RequestInterrupted


MODEL = 'gpt-6-astra'
OUTPUT = [
    {'type': 'reasoning', 'id': 'rs_1', 'summary': [{'type': 'summary_text', 'text': 'Inspecting.'}],
     'encrypted_content': 'opaque-reasoning'},
    {'type': 'function_call', 'id': 'fc_1', 'call_id': 'call_1',
     'name': 'read_image', 'arguments': '{"file":"image.png"}', 'status': 'completed'},
]
TOOL = {'type': 'function', 'function': {
    'name': 'read_image', 'parameters': {'type': 'object', 'properties': {
        'file': {'type': 'string'}, 'optional': {'type': 'string'},
    }, 'required': ['file']},
}}


def _response(output=None, **kwargs):
    return {'id': 'resp_1', 'object': 'response', 'created_at': 0,
            'model': MODEL, 'status': 'completed', 'output': OUTPUT if output is None else output,
            'usage': {'input_tokens': 100, 'output_tokens': 25, 'total_tokens': 125,
                      'input_tokens_details': {'cached_tokens': 50},
                      'output_tokens_details': {'reasoning_tokens': 20}}, **kwargs}


def _sse(events):
    return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=''.join(
        'data: ' + json.dumps(event) + '\n\n' for event in events
    ))


def _provider(monkeypatch, handler):
    provider = create_provider('openai', ProviderSettings(
        api_key='test-key', base_url='https://gateway.test/prefix',
        api='responses', reasoning_style='openai-astra',
    ))
    clients = []

    def client():
        client = OpenAI(api_key='test-key', base_url='https://gateway.test/prefix',
                        max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        clients.append(client)
        return client

    monkeypatch.setattr(provider, '_new_client', client)
    return provider, clients


def test_api_selection_defaults_and_validation():
    assert type(create_provider('openai', ProviderSettings())) is OpenAICompatibleProvider
    assert isinstance(create_provider('openai', ProviderSettings(api='responses')), OpenAIResponsesProvider)
    for invalid in ('response', '', None):
        with pytest.raises(ValueError, match='api must be'):
            create_provider('openai', ProviderSettings(api=invalid))


@pytest.mark.parametrize('streaming', [False, True])
def test_responses_tool_round_trip_with_images_and_encrypted_state(monkeypatch, streaming):
    requests = []

    def handler(request):
        assert request.url == 'https://gateway.test/prefix/responses'
        assert request.headers['authorization'] == 'Bearer test-key'
        body = json.loads(request.content)
        requests.append(body)
        output = OUTPUT if len(requests) == 1 else [
            {'type': 'message', 'id': 'msg_1', 'role': 'assistant', 'status': 'completed',
             'content': [{'type': 'output_text', 'text': 'Red.', 'annotations': []}]},
        ]
        response = _response(output)
        if body['stream']:
            return _sse([
                {'type': 'response.reasoning_summary_text.delta', 'delta': 'Inspecting.'},
                {'type': 'response.output_text.delta', 'delta': 'Red.' if len(requests) > 1 else ''},
                {'type': 'response.completed', 'response': response},
            ])
        return httpx.Response(200, json=response)

    provider, clients = _provider(monkeypatch, handler)
    req = CompletionRequest(
        model=MODEL, messages=[Message.system('Be precise.'), Message.user('Inspect the image.')],
        tools=[TOOL], stream=streaming, max_output_tokens=2048, reasoning_effort='max',
        session_id='session-1', timeout=3,
    )
    thinking, content = [], []

    def complete(request):
        if not request.stream:
            return provider.complete(request)
        stream = provider.open_stream(request)
        try:
            return stream.consume(on_content=content.append, on_thinking=thinking.append)
        finally:
            stream.close()
            stream.close()

    result = complete(req)
    assert result.finish_reason == 'tool_calls'
    assert result.message.tool_calls[0].id == 'call_1'
    assert result.usage.cached_prompt_tokens == 50
    assert result.usage.reasoning_tokens == 20
    # Replay survives the same wire round-trip used by session persistence.
    restored = Message.from_wire(json.loads(json.dumps(result.message.to_wire())))
    req = replace(req, messages=[*req.messages, restored, Message.tool('call_1', 'Image loaded'),
                                Message.user([TextPart('Image'), ImagePart({'url': 'data:image/png;base64,AA==', 'detail': 'original'})])])
    result = complete(req)
    assert result.message.text == 'Red.'
    assert result.finish_reason == 'stop'
    first, second = requests
    assert first['instructions'] == 'Be precise.'
    assert first['max_output_tokens'] == 2048
    assert first['reasoning']['effort'] == 'max'
    assert first['store'] is False
    assert first['include'] == ['reasoning.encrypted_content']
    assert first['tools'][0]['strict'] is False  # optional tool arguments remain optional
    assert first['tools'][0]['parameters']['required'] == ['file']
    assert 'timeout' not in first
    assert 'provider_state' not in json.dumps(second)
    replayed = second['input'][1:3]
    assert replayed[0]['encrypted_content'] == 'opaque-reasoning'
    assert replayed[1]['call_id'] == 'call_1'
    assert second['input'][3] == {'type': 'function_call_output', 'call_id': 'call_1', 'output': 'Image loaded'}
    assert second['input'][-1]['content'][-1] == {
        'type': 'input_image', 'image_url': 'data:image/png;base64,AA==', 'detail': 'original',
    }
    assert all(client.is_closed() for client in clients)
    if streaming:
        assert thinking == ['Inspecting.', 'Inspecting.']
        assert content == ['Red.']

    # Opaque state cannot cross between Codex and the API-key provider or models.
    assert 'opaque-reasoning' not in json.dumps(_build_body(req))
    assert 'opaque-reasoning' not in json.dumps(provider._kwargs(replace(req, model='other-model')))


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize(('reason', 'finish_reason'), [
    ('max_output_tokens', 'length'), ('content_filter', 'content_filter'),
])
def test_responses_structured_output_and_incomplete_response(monkeypatch, streaming, reason, finish_reason):
    schema = {'type': 'object', 'properties': {'color': {'type': 'string'}},
              'required': ['color'], 'additionalProperties': False}
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        response = _response(status='incomplete', incomplete_details={'reason': reason})
        if streaming:
            return _sse([{'type': 'response.incomplete', 'response': response}])
        return httpx.Response(200, json=response)

    provider, clients = _provider(monkeypatch, handler)
    request = CompletionRequest(
        model=MODEL, messages=[Message.user('Classify')], stream=streaming, response_schema=schema,
    )
    if streaming:
        stream = provider.open_stream(request)
        try:
            result = stream.consume()
        finally:
            stream.close()
    else:
        result = provider.complete(request)
    assert seen[0]['text']['format'] == {
        'type': 'json_schema', 'name': 'batch_item', 'strict': True, 'schema': schema,
    }
    assert 'tools' not in seen[0]
    assert 'tool_choice' not in seen[0]
    assert result.finish_reason == finish_reason
    assert result.message.provider_state is None
    assert clients[0].is_closed()


@pytest.mark.parametrize('mode', ['cancel', 'partial', 'failed', 'error', 'http_error'])
def test_responses_stream_termination_and_cleanup(monkeypatch, mode):
    def handler(request):
        if mode == 'http_error':
            return httpx.Response(400, json={'error': {'message': 'Bad request', 'type': 'invalid_request_error'}})
        events = [{'type': 'response.output_text.delta', 'delta': 'Partial'}]
        if mode == 'failed':
            events.append({'type': 'response.failed', 'response': _response(status='failed', error={'message': 'Failed'})})
        elif mode == 'error':
            events.append({'type': 'error', 'message': 'Failed', 'code': 'server_error'})
        return _sse(events)

    provider, clients = _provider(monkeypatch, handler)
    request = CompletionRequest(model=MODEL, messages=[Message.user('Hi')])
    if mode == 'http_error':
        from openai import BadRequestError
        with pytest.raises(BadRequestError):
            provider.open_stream(request)
    else:
        stream = provider.open_stream(request)
        try:
            if mode == 'cancel':
                with pytest.raises(RequestInterrupted):
                    stream.consume(should_stop=lambda: True)
                provider.cancel()
            elif mode in ('failed', 'error'):
                with pytest.raises(ProviderError, match='Failed'):
                    stream.consume()
            else:
                result = stream.consume()
                assert result.message.text == 'Partial'
                assert result.finish_reason is None
                assert result.message.provider_state is None
        finally:
            stream.close()
            stream.close()
    assert provider._active_client is None
    assert all(client.is_closed() for client in clients)


def test_responses_nonreasoning_models_omit_reasoning_controls():
    provider = OpenAIResponsesProvider(ProviderSettings(api='responses'))
    body = provider._kwargs(CompletionRequest(
        model='gpt-4o', messages=[Message.user('Hello')], reasoning_effort='high',
    ))
    assert 'reasoning' not in body
    assert 'include' not in body
    assert 'text' not in body


def test_model_switch_selects_configured_api_and_rejects_invalid_mode(monkeypatch):
    from types import SimpleNamespace as NS
    from ene.backend.commands import AgentCommandsMixin
    from ene.config import conf
    from ene.ui import AgentConsole

    monkeypatch.setitem(conf, 'openai', {
        'responses': {'model': MODEL, 'api': 'responses'},
        'chat': {'model': MODEL},
        'invalid': {'model': MODEL, 'api': 'typo'},
    })
    agent = NS(
        model_alias='old', provider=OpenAICompatibleProvider(ProviderSettings()),
        tool_executor=NS(supports_image_input=True), reasoning_effort='high',
        presence=NS(update=lambda **kwargs: None), console=AgentConsole(render_terminal=False),
    )
    AgentCommandsMixin._cmd_model(agent, '/model responses')
    assert isinstance(agent.provider, OpenAIResponsesProvider)
    assert agent._provider_settings.api == 'responses'
    previous = agent.provider
    AgentCommandsMixin._cmd_model(agent, '/model invalid')
    assert agent.provider is previous
    assert agent.model_alias == 'responses'
    AgentCommandsMixin._cmd_model(agent, '/model chat')
    assert type(agent.provider) is OpenAICompatibleProvider
    assert agent._provider_settings.api == 'chat_completions'
