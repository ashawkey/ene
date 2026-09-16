"""Image tool results remain visual context across requests and sessions."""

import base64
import json
from types import SimpleNamespace as NS

import pytest

from ene.backend import LLMAgent, TurnOutcome
from ene.context import (
    DEFAULT_CHARS_PER_TOKEN, ESTIMATED_IMAGE_TOKENS,
    ContextManager, TokenEstimator, compact_context, needs_compaction,
)
from ene.messages import ImagePart, Message, ToolCall
from ene.models import resolve_model_profile
from ene.providers import CompletionResult, ProviderUsage
from ene.providers.openai_codex import _build_body
from ene.providers.openai_compatible import OpenAICompatibleProvider
from ene.providers.registry import ProviderSettings
from ene.providers.types import CompletionRequest
from ene.session_store import SessionStore
from ene.tools import ToolExecutor
from ene.ui import AgentConsole


PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII='
)
URL = 'data:image/png;base64,' + base64.b64encode(PNG).decode('ascii')


def _agent(tmp_path):
    (tmp_path / 'image.png').write_bytes(PNG)
    console = AgentConsole(render_terminal=False)
    return NS(
        context=ContextManager('system'),
        console=console,
        tool_executor=ToolExecutor(console=console, work_dir=str(tmp_path)),
        token_estimator=TokenEstimator(),
        work_dir=str(tmp_path),
        context_length=0,
        max_output_tokens=1000,
        model='gpt-6-astra',
        profile=resolve_model_profile('gpt-6-astra'),
        tools=[],
        stream=False,
        reasoning_effort=None,
        cancellation=None,
        verbose=False,
        round_id=1,
        _session_id=None,
        INITIAL_BACKOFF=0,
        MAX_BACKOFF=0,
        _interruptible_sleep=lambda _: None,
        _accumulate_usage=lambda _: None,
    )


def _images(messages):
    return [part.image_url for message in messages
            if isinstance(message.content, list)
            for part in message.content if isinstance(part, ImagePart)]


def _read_batch(agent):
    calls = [
        ToolCall('image', 'read_image', json.dumps({'file': 'image.png'})),
        ToolCall('missing', 'read_image', json.dumps({'file': 'missing.png'})),
    ]
    agent.context.add(Message.user('Inspect this image.'))
    agent.context.add(Message.assistant(tool_calls=calls))
    assert LLMAgent.execute_tool_calls(agent, calls) == TurnOutcome.COMPLETED
    assert [m.role for m in agent.context.messages] == [
        'user', 'assistant', 'tool', 'tool', 'user',
    ]
    assert _images(agent.context.messages) == [{'url': URL}]
    assert not agent.context.messages[-1].is_user_input


@pytest.mark.parametrize('model', ['gpt-6-astra', 'gpt-5.6-sol'])
def test_image_survives_retry_tool_round_and_followup(tmp_path, model):
    agent = _agent(tmp_path)
    agent.model = model
    _read_batch(agent)
    requests = []

    def complete(request):
        requests.append(request)
        if len(requests) == 1:
            raise RuntimeError('temporary transport failure')
        return CompletionResult(
            Message.assistant('Observed image'),
            ProviderUsage(1000, 10, 1010), 'stop',
        )

    agent._blocking_completion = complete
    LLMAgent.call_api(agent)
    call = ToolCall('file', 'read_file', json.dumps({'file': 'missing.txt'}))
    agent.context.add(Message.assistant(tool_calls=[call]))
    LLMAgent.execute_tool_calls(agent, [call])
    LLMAgent.call_api(agent)
    agent.context.add(Message.user('Look at the image again.'))
    LLMAgent.call_api(agent)

    assert len(requests) == 4
    for request in requests:
        assert _images(request.messages) == [{'url': URL}]
        chat = OpenAICompatibleProvider(ProviderSettings())._kwargs(request)
        responses = _build_body(request)
        assert sum(part.get('type') == 'image_url'
                   for message in chat['messages']
                   if isinstance(message.get('content'), list)
                   for part in message['content']) == 1
        images = [part for message in responses['input']
                  for part in message.get('content', [])
                  if part.get('type') == 'input_image']
        assert images == [{'type': 'input_image', 'detail': 'auto', 'image_url': URL}]
    assert _images(agent.context.messages) == [{'url': URL}]
    # Image token costs must not calibrate the text-only character ratio.
    assert agent.token_estimator.anchored
    assert agent.token_estimator.chars_per_token == DEFAULT_CHARS_PER_TOKEN
    assert 1000 <= LLMAgent._context_tokens(agent) < 1100


def test_image_survives_session_reload_and_rewind(tmp_path):
    agent = _agent(tmp_path)
    store = SessionStore(tmp_path / 'sessions', 'images')
    before, _, _ = store.commit(
        {'messages': [], 'round_id': 0}, parent_id=None,
        code_parent_id=None, changes=[], reason='round',
    )
    _read_batch(agent)
    after, _, _ = store.commit(
        {'messages': agent.context.messages, 'round_id': 1}, parent_id=before,
        code_parent_id=None, changes=[], reason='round',
    )
    (tmp_path / 'image.png').unlink()
    reloaded = SessionStore(tmp_path / 'sessions', 'images')
    assert reloaded.revision_prompt(after) == 'Inspect this image.'
    assert reloaded.candidates()[0]['prompt'] == 'Inspect this image.'
    assert reloaded.summary()['last_user_message'] == 'Inspect this image.'
    agent.context.replace_messages(reloaded.materialize(after)['messages'])
    assert _images(agent.context.get()) == [{'url': URL}]
    assert not agent.context.messages[-1].is_user_input
    assert LLMAgent._context_tokens(agent) >= ESTIMATED_IMAGE_TOKENS
    request = CompletionRequest(model=agent.model, messages=agent.context.get())
    assert _build_body(request)['input'][-1]['content'][-1]['image_url'] == URL
    agent.context.replace_messages(reloaded.materialize(before)['messages'])
    assert _images(agent.context.get()) == []
    assert LLMAgent._context_tokens(agent) < ESTIMATED_IMAGE_TOKENS


def test_completed_image_is_retained_when_later_tool_interrupts(tmp_path):
    agent = _agent(tmp_path)
    execute = agent.tool_executor.execute

    def interrupt(name, args):
        if name == 'wait':
            return {'success': False, 'interrupted': True, 'error': 'Cancelled'}
        return execute(name, args)

    agent.tool_executor.execute = interrupt
    calls = [
        ToolCall('image', 'read_image', '{"file":"image.png"}'),
        ToolCall('wait', 'wait', '{"seconds":1}'),
        ToolCall('skipped', 'read_image', '{"file":"image.png"}'),
    ]
    agent.context.add(Message.assistant(tool_calls=calls))
    assert LLMAgent.execute_tool_calls(agent, calls) == TurnOutcome.USER_INTERRUPTED
    assert [m.role for m in agent.context.messages] == [
        'assistant', 'tool', 'tool', 'tool', 'user',
    ]
    assert _images(agent.context.messages) == [{'url': URL}]


def test_image_heavy_history_compacts_and_reduces_live_estimate(tmp_path):
    agent = _agent(tmp_path)
    agent.context_length = 128_000
    agent.context.add(Message.user('Inspect these screenshots.'))
    for _ in range(100):
        agent.context.add(Message.user([ImagePart({'url': URL})]))
        agent.context.add(Message.assistant('Inspected.'))
    before = LLMAgent._context_tokens(agent)
    assert before >= 100 * ESTIMATED_IMAGE_TOKENS
    assert needs_compaction(agent.context.messages, agent.context_length)

    # Real multimodal usage must anchor the estimate without distorting the
    # text ratio. The compactor must count images in its split and yield tests.
    agent.token_estimator.observe(agent.context.total_chars, 150_000, 100)
    assert LLMAgent._context_tokens(agent) == 150_000
    compacted, state = compact_context(
        agent.context.messages, lambda _: 'Inspected older screenshots.',
        context_length=agent.context_length, used_tokens=150_000,
    )
    assert compacted is not agent.context.messages
    agent.context.replace_messages(compacted)
    assert 0 < agent.context.image_count < 50
    assert LLMAgent._context_tokens(agent) < 128_000 // 2
    assert not needs_compaction(agent.context.messages, agent.context_length)
    assert state.original_request == 'Inspect these screenshots.'
    assert agent.token_estimator.chars_per_token == DEFAULT_CHARS_PER_TOKEN


def test_image_payload_is_hidden_from_replay_recap_and_live_preview(tmp_path):
    import threading
    from ene.backend.commands import AgentCommandsMixin
    from ene.backend.sessions import SessionMixin
    from ene.live_worker import Worker

    agent = _agent(tmp_path)
    _read_batch(agent)
    users = []
    agent.console = NS(system=lambda _: None, user_input=users.append, response=lambda _: None)
    SessionMixin._replay_context(agent)
    assert users == ['Inspect this image.']
    assert AgentCommandsMixin._recap_input(agent) == 'Opening request:\nInspect this image.'
    worker = NS(agent=agent, _state_lock=threading.Lock(), _last_user_message='')
    Worker._seed_last_user_message(worker)
    assert worker._last_user_message == 'Inspect this image.'


def test_user_authored_image_remains_a_user_input():
    message = Message.user([ImagePart({'url': URL})])
    assert message.is_user_input
    assert Message.from_wire(message.to_wire()).is_user_input
    context = ContextManager('system')
    context.add(message)
    projected = context.get(include_images=False)[-1]
    assert projected.text == '[Images omitted for a text-only model.]'
    assert projected.is_user_input
    assert not projected.image_count
    assert message.image_count == 1


@pytest.mark.parametrize('api', ['chat_completions', 'responses'])
def test_model_switch_omits_images_only_from_text_model_requests(monkeypatch, tmp_path, api):
    from ene.backend.commands import AgentCommandsMixin
    from ene.config import conf

    monkeypatch.setitem(conf, 'openai', {
        'vision': {'model': 'gpt-6-astra', 'api': api, 'context_length': 0},
        'text': {'model': 'deepseek-v4-flash', 'api': api, 'context_length': 0},
    })
    agent = _agent(tmp_path)
    agent.model_alias = 'vision'
    agent.provider = OpenAICompatibleProvider(ProviderSettings())
    agent.presence = NS(update=lambda **kwargs: None)
    agent._context_tokens = lambda: LLMAgent._context_tokens(agent)
    agent._estimate_usage = lambda message: LLMAgent._estimate_usage(agent, message)
    _read_batch(agent)
    image_tokens = agent._context_tokens()
    requests, bodies = [], []

    def complete(request):
        requests.append(request)
        bodies.append(agent.provider._kwargs(request))
        return CompletionResult(Message.assistant('Done'), None, 'stop')

    agent._blocking_completion = complete
    AgentCommandsMixin._cmd_model(agent, '/model text')
    assert agent._context_tokens() < image_tokens
    LLMAgent.call_api(agent)
    assert not _images(requests[-1].messages)
    assert 'data:image/' not in json.dumps(bodies[-1])
    assert '[Images omitted for a text-only model.]' in json.dumps(bodies[-1])
    assert _images(agent.context.messages) == [{'url': URL}]

    AgentCommandsMixin._cmd_model(agent, '/model vision')
    LLMAgent.call_api(agent)
    assert _images(requests[-1].messages) == [{'url': URL}]
    assert URL in json.dumps(bodies[-1])
    assert '[Images omitted for a text-only model.]' not in json.dumps(bodies[-1])
    assert 'display_content' not in json.dumps(bodies[-1])


def test_image_allowance_is_separate_from_text_calibration():
    estimator = TokenEstimator()
    estimator.observe(4000, 1000)
    assert estimator.chars_per_token == 4
    assert estimator.prompt_tokens(4000, 1) == 1000 + ESTIMATED_IMAGE_TOKENS
    estimator.observe(4000, 1600, 1)
    assert estimator.chars_per_token == 4
    assert estimator.prompt_tokens(4400, 1) == 1700
    assert estimator.prompt_tokens(4400, 2) == 1700 + ESTIMATED_IMAGE_TOKENS
    # Removing an image whose real cost was less than the allowance must not
    # subtract the allowance from the measured count and erase text usage.
    assert estimator.prompt_tokens(4000, 0) == 1000
