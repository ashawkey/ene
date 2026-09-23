"""Configured model aliases resolve consistently at startup and during a session."""

from types import SimpleNamespace as NS

import pytest

from ene import cli, config
from ene.backend import LLMAgent
from ene.backend.commands import AgentCommandsMixin
from ene.models import resolve_model_alias
from ene.providers import OpenAIResponsesProvider


@pytest.mark.parametrize(('name', 'aliases', 'expected'), [
    ('gpt-6', ['gpt-6-astra', 'gpt-5.6-sol'], 'gpt-6-astra'),
    ('gpt-6', ['gpt-6-astra', 'gpt-6'], 'gpt-6'),
    ('gpt-6-astra', ['gpt-6-astra'], 'gpt-6-astra'),
])
def test_alias_resolution_prefers_exact_then_unique_prefix(name, aliases, expected):
    assert resolve_model_alias(name, iter(aliases)) == expected


@pytest.mark.parametrize('name', ['missing', 'GPT-6', 'astra', ''])
def test_alias_resolution_does_not_guess(name):
    with pytest.raises(ValueError, match='not found'):
        resolve_model_alias(name, ['gpt-6-astra'])


def test_ambiguous_prefix_lists_matching_aliases_only():
    with pytest.raises(ValueError) as error:
        resolve_model_alias('gpt', ['gpt-6-astra', 'gpt-5.6-sol', 'claude'])
    assert str(error.value) == "Ambiguous model 'gpt'. Matches: gpt-6-astra, gpt-5.6-sol"


@pytest.mark.parametrize(('name', 'expected'), [
    ('gpt-6', 'gpt-6-astra'), ('gpt-6-astra', 'gpt-6-astra'),
    ('gpt', None), ('missing', None),
])
def test_cli_resolves_alias_before_constructing_agent(monkeypatch, name, expected):
    monkeypatch.setitem(cli.conf, 'openai', {
        'gpt-6-astra': {'model': 'azure/openai/gpt-6-astra', 'api': 'responses'},
        'gpt-5.6-sol': {'model': 'openai/gpt-5.6-sol'},
    })
    for key in ('recap_model', 'summary_model'):
        monkeypatch.delitem(cli.conf, key, raising=False)
    errors, created = [], []
    monkeypatch.setattr(cli, 'AgentConsole', lambda: NS(error=errors.append))
    monkeypatch.setattr(cli, 'LLMAgent', lambda **kwargs: created.append(kwargs) or NS())
    args = cli.Args(model=name)
    result = cli.get_agent(args)
    if expected is None:
        assert result is None
        assert not created
        assert ('Ambiguous' if name == 'gpt' else 'not found') in errors[0]
    else:
        assert result is not None
        assert args.model == expected
        assert created[0]['model_alias'] == expected
        assert created[0]['model'] == 'azure/openai/gpt-6-astra'
        assert created[0]['api'] == 'responses'
        assert not errors


def test_switch_resolves_prefix_and_preserves_state_on_ambiguity(monkeypatch):
    monkeypatch.setitem(config.conf, 'openai', {
        'gpt-6-astra': {'model': 'azure/openai/gpt-6-astra', 'api': 'responses'},
        'gpt-5.6-sol': {'model': 'openai/gpt-5.6-sol'},
    })
    errors, notices, closed = [], [], []
    agent = NS(
        model_alias='old', provider=NS(close=lambda: closed.append(True)),
        console=NS(error=errors.append, system=notices.append),
        tool_executor=NS(supports_image_input=False), reasoning_effort='high',
        presence=NS(update=lambda **kwargs: None),
    )
    AgentCommandsMixin._cmd_model(agent, '/model gpt-6')
    assert agent.model_alias == 'gpt-6-astra'
    assert isinstance(agent.provider, OpenAIResponsesProvider)
    assert closed == [True]
    previous = agent.provider
    AgentCommandsMixin._cmd_model(agent, '/model gpt')
    assert agent.provider is previous
    assert agent.model_alias == 'gpt-6-astra'
    assert errors == ["Ambiguous model 'gpt'. Matches: gpt-6-astra, gpt-5.6-sol"]
    AgentCommandsMixin._cmd_model(agent, '/model gpt-6')
    assert agent.provider is previous
    assert notices[-1] == "Already using model 'gpt-6-astra'."


@pytest.mark.parametrize('alias, model_conf, context_length, max_output, reasoning', [
    ('claude', {'model': 'azure/openai/GPT-6-SOL'}, 1_050_000, 128_000, 'openai-6'),
    ('gpt-6-astra', {'model': 'custom-deployment'}, 128_000, 32_000, None),
    ('gpt-6-sol', {}, 1_050_000, 128_000, 'openai-6'),
    ('small', {'model': 'gpt-6-luna', 'context_length': 200_000,
               'max_output_tokens': 16_000}, 200_000, 16_000, 'openai-6'),
])
def test_model_budgets_use_api_id_at_startup_switch_and_listing(
    monkeypatch, tmp_path, alias, model_conf, context_length, max_output, reasoning,
):
    monkeypatch.setattr(config, 'conf', {'openai': {alias: model_conf}})
    monkeypatch.setattr(cli, 'conf', config.conf)
    model = model_conf.get('model', alias)
    agent = LLMAgent(
        api_key='', base_url='', model=model, model_alias=alias, work_dir=str(tmp_path),
        terminal_prompts=False,
        context_length=model_conf.get('context_length'),
        max_output_tokens=model_conf.get('max_output_tokens'),
    )
    try:
        assert agent.context_length == context_length
        assert agent.max_output_tokens == max_output
        assert agent.profile.reasoning == reasoning
        # Switch through the same config path used by /model.
        agent.model_alias = 'previous'
        agent._cmd_model(f'/model {alias}')
        assert agent.model == model
        assert agent.context_length == context_length
        assert agent.max_output_tokens == max_output
        assert agent.profile.reasoning == reasoning
    finally:
        agent.close()

    tables = []
    monkeypatch.setattr(cli, 'AgentConsole', lambda: NS(table=tables.append))
    cli.cmd_models()
    columns = {column.header: column._cells for column in tables[0].columns}
    assert columns['Model'] == [model]
    assert columns['Context'] == [f'{context_length // 1000}K']
