"""Tests for direct model completions and the bundled batch skill."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from ene.backend.batch import BatchCompletionMixin
from ene.messages import Message
from ene.providers import CompletionResult, ProviderUsage
from ene.skills import BUNDLED_SKILLS_DIR, load_skill_tools
from ene.tools import ToolExecutor

BATCH_SKILL_DIR = BUNDLED_SKILLS_DIR / "batch"


def _load_batch_tool():
    entries = load_skill_tools(BATCH_SKILL_DIR)
    assert len(entries) == 1
    return entries[0]


class _Indicator:
    def __init__(self, suffix=""):
        self.suffixes = [suffix]

    def set_status_suffix(self, suffix):
        self.suffixes.append(suffix)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _Console:
    def __init__(self):
        self.indicators = []
        self.calls = []

    def thinking(self, **kwargs):
        self.calls.append(kwargs)
        indicator = _Indicator(kwargs.get("status_suffix", ""))
        self.indicators.append(indicator)
        return indicator

    def tool(self, *args, **kwargs):
        pass


class _Cancellation:
    cancelled = False
    watch_keyboard = False


class _Completer:
    def __init__(self, responses=None, delay=0):
        self.responses = responses or {}
        self.delay = delay
        self.calls = []
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def __call__(self, instruction, item, **kwargs):
        with self.lock:
            self.calls.append((instruction, item, kwargs))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            value = self.responses.get(item, f"result:{item}")
            if isinstance(value, Exception):
                raise value
            return {"result": value, "usage": {"total_tokens": 3}}
        finally:
            with self.lock:
                self.active -= 1


def _executor(tmp_path, completer=None, *, image=True, console=None):
    return ToolExecutor(
        console=console or _Console(),
        work_dir=str(tmp_path),
        cancellation=_Cancellation(),
        batch_completion=completer or _Completer(),
        cancel_batch_completions=lambda: None,
        supports_image_input=image,
    )


def _output(tmp_path, name="run"):
    return tmp_path / ".ene" / "batch" / f"{name}.jsonl"


def _records(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_batch_runs_direct_completions_in_parallel_and_returns_only_summary(tmp_path):
    entry = _load_batch_tool()
    completer = _Completer(delay=0.04)
    console = _Console()
    executor = _executor(tmp_path, completer, console=console)

    result = entry["run"](
        executor,
        instruction="Classify {item}",
        items=["a", "b", "c", "d"],
        name="classes",
        concurrency=3,
        label="Classifying",
    )

    assert result["success"] and result["succeeded"] == 4
    assert completer.max_active >= 2
    assert "result:a" not in json.dumps(result)
    records = _records(_output(tmp_path, "classes"))
    assert {record["result"] for record in records} == {
        "result:Classify a", "result:Classify b", "result:Classify c", "result:Classify d"
    }
    assert all(record["usage"] == {"total_tokens": 3} for record in records)
    assert console.calls[0]["label"] == "Classifying"
    assert any("4/4 completed" in suffix for suffix in console.indicators[0].suffixes)


def test_batch_without_placeholder_sends_shared_instruction_and_item(tmp_path):
    entry = _load_batch_tool()
    completer = _Completer()
    executor = _executor(tmp_path, completer)

    entry["run"](
        executor, instruction="Translate to French", items=["hello"], name="translation"
    )

    assert completer.calls[0][0:2] == ("Translate to French", "hello")


def test_batch_passes_schema_and_reasoning_controls(tmp_path):
    entry = _load_batch_tool()
    completer = _Completer(responses={"x": {"label": "yes"}})
    executor = _executor(tmp_path, completer)
    schema = {
        "type": "object",
        "properties": {"label": {"type": "string"}},
        "required": ["label"],
        "additionalProperties": False,
    }

    entry["run"](
        executor,
        instruction="Classify",
        items=["x"],
        name="structured",
        output_schema=schema,
        max_output_tokens=40,
        reasoning_effort="minimal",
    )

    kwargs = completer.calls[0][2]
    assert kwargs["output_schema"] == schema
    assert kwargs["max_output_tokens"] == 40
    assert kwargs["reasoning_effort"] == "minimal"
    assert _records(_output(tmp_path, "structured"))[0]["result"] == {"label": "yes"}


def test_batch_reads_local_images_without_an_agent_tool_round(tmp_path):
    entry = _load_batch_tool()
    completer = _Completer()
    executor = _executor(tmp_path, completer)
    # Minimal valid GIF header accepted by the image loader.
    (tmp_path / "one.gif").write_bytes(b"GIF89a" + b"\x00" * 20)

    result = entry["run"](
        executor,
        instruction="Caption this image",
        items=["one.gif"],
        item_type="image",
        name="captions",
    )

    assert result["succeeded"] == 1
    call = completer.calls[0]
    assert call[0:2] == ("Caption this image", "one.gif")
    assert call[2]["image_url"].startswith("data:image/gif;base64,")


def test_batch_rejects_image_mode_for_text_only_model(tmp_path):
    result = _load_batch_tool()["run"](
        _executor(tmp_path, image=False),
        instruction="Caption",
        items=["a.png"],
        item_type="image",
        name="captions",
    )
    assert result["success"] is False
    assert "does not support image" in result["error"]


def test_batch_resume_skips_successes_and_retries_failures(tmp_path):
    entry = _load_batch_tool()
    output = _output(tmp_path)
    output.parent.mkdir(parents=True)
    output.write_text(
        json.dumps({"item": "a", "index": 1, "ok": True, "result": "kept"}) + "\n"
        + json.dumps({"item": "b", "index": 2, "ok": False, "error": "old"}) + "\n"
        + '{"item":"c","ok":tru',
        encoding="utf-8",
    )
    completer = _Completer()

    result = entry["run"](
        _executor(tmp_path, completer),
        instruction="Do",
        items=["a", "b", "c"],
        name="run",
    )

    assert result["skipped"] == 1 and result["succeeded"] == 2
    assert {call[1] for call in completer.calls} == {"b", "c"}


def test_batch_restart_preserves_old_results(tmp_path):
    entry = _load_batch_tool()
    output = _output(tmp_path)
    output.parent.mkdir(parents=True)
    old = json.dumps({"item": "a", "ok": True, "result": "old"}) + "\n"
    output.write_text(old, encoding="utf-8")

    result = entry["run"](
        _executor(tmp_path), instruction="Redo", items=["a"], name="run", resume=False
    )

    assert output.with_name("run.jsonl.bak").read_text() == old
    assert "run.jsonl.bak" in result["message"]


def test_batch_records_failures_and_keeps_other_results(tmp_path):
    completer = _Completer({"bad": RuntimeError("rejected")})
    result = _load_batch_tool()["run"](
        _executor(tmp_path, completer),
        instruction="Do",
        items=["good", "bad", "also-good"],
        name="run",
    )

    assert (result["succeeded"], result["failed"]) == (2, 1)
    assert result["failures"][0]["item"] == "bad"
    assert {record["ok"] for record in _records(_output(tmp_path))} == {True, False}


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"instruction": "", "items": ["a"], "name": "run"}, "instruction is required"),
        ({"instruction": "x", "items": ["a"], "name": "../bad"}, "name is required"),
        ({"instruction": "x", "name": "run"}, "exactly one"),
        ({"instruction": "x", "items": ["a"], "name": "run", "concurrency": 0}, "concurrency"),
        ({"instruction": "x", "items": ["a"], "name": "run", "item_type": "file"}, "item_type"),
        ({"instruction": "x", "items": ["a"], "name": "run", "output_schema": []}, "output_schema"),
    ],
)
def test_batch_rejects_invalid_arguments(tmp_path, kwargs, message):
    result = _load_batch_tool()["run"](_executor(tmp_path), **kwargs)
    assert result["success"] is False
    assert message in result["error"]


def test_batch_requires_agent_backed_completion_service(tmp_path):
    executor = ToolExecutor(console=_Console(), work_dir=str(tmp_path))
    result = _load_batch_tool()["run"](
        executor, instruction="Do", items=["a"], name="run"
    )
    assert result["success"] is False
    assert "not available" in result["error"]


def test_direct_completion_uses_fresh_context_provider_and_parses_json(monkeypatch):
    requests = []

    class Provider:
        def complete(self, request):
            requests.append(request)
            return CompletionResult(
                Message.assistant('{"label":"yes"}'),
                ProviderUsage(10, 2, 12),
                "stop",
            )

        def close(self):
            pass

        def cancel(self):
            pass

    monkeypatch.setattr("ene.backend.batch.create_provider", lambda *args: Provider())
    usage = []
    agent = NS(
        profile=NS(supports_image_input=True),
        cancellation=_Cancellation(),
        model="demo",
        max_output_tokens=100,
        reasoning_effort="high",
        provider_name="openai",
        _provider_settings=object(),
        _batch_provider_lock=threading.Lock(),
        _batch_providers=set(),
        _session_id="session",
        _accumulate_usage=usage.append,
    )
    schema = {"type": "object"}

    result = BatchCompletionMixin.run_batch_completion(
        agent, "Classify", "item", output_schema=schema, reasoning_effort="low"
    )

    assert result["result"] == {"label": "yes"}
    request = requests[0]
    assert request.messages == [Message.system("Classify"), Message.user("item")]
    assert request.tools == [] and request.stream is False
    assert request.response_schema == schema and request.reasoning_effort == "low"
    assert usage[0].total_tokens == 12
