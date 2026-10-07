"""Tests for streamed tool-call reassembly with irregular indices."""

from types import SimpleNamespace as NS

from ene.utils.streaming import consume_stream


def _chunk(tool_calls):
    delta = NS(
        role=None, content=None, reasoning=None, reasoning_content=None,
        tool_calls=tool_calls, model_extra={},
    )
    return NS(choices=[NS(delta=delta, finish_reason=None)], usage=None)


def _tc(index, id=None, name=None, args=None):
    return NS(index=index, id=id, function=NS(name=name, arguments=args))


def _calls(stream):
    message, _, _ = consume_stream(stream)
    return [(call.id, call.name, call.arguments) for call in message.tool_calls]


def test_new_id_under_a_reused_index_starts_a_new_call():
    stream = [
        _chunk([_tc(0, id="a", name="one", args='{"x":')]),
        _chunk([_tc(0, args=" 1}")]),
        _chunk([_tc(0, id="b", name="two", args="{}")]),
    ]
    assert _calls(stream) == [("a", "one", '{"x": 1}'), ("b", "two", "{}")]


def test_missing_or_none_index_falls_back_to_ids():
    stream = [
        _chunk([NS(id="a", function=NS(name="one", arguments='{"x":'))]),
        _chunk([_tc(None, args=" 1}")]),
        _chunk([_tc(None, id="b", name="two", args="{}")]),
    ]
    assert _calls(stream) == [("a", "one", '{"x": 1}'), ("b", "two", "{}")]


def test_interleaved_indices_still_accumulate_separately():
    stream = [
        _chunk([_tc(0, id="a", name="one"), _tc(1, id="b", name="two")]),
        _chunk([_tc(0, args="{}"), _tc(1, args='{"y": 2}')]),
    ]
    assert _calls(stream) == [("a", "one", "{}"), ("b", "two", '{"y": 2}')]
