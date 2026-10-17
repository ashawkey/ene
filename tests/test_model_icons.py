"""Model-family status markers use API IDs, independently of aliases/transport."""

import pytest

from ene.models import MODEL_CATALOG, model_icon
from ene.ui import ContextStatus


@pytest.mark.parametrize(("model", "icon"), [
    ("anthropic/Claude-Opus-5-5", "✳︎"),
    ("azure/openai/GPT-6-SOL", "🔷"),
    ("gpt-5-codex", "🔷"),
    ("openai/o3-mini", "🔷"),
    ("o1", "🔷"),
    ("o4-mini", "🔷"),
    ("google/gemini-3-pro", "✨"),
    ("deepseek/DeepSeek-V4-Pro", "🐋"),
    ("z-ai/glm-5.3", "🧠"),
    ("moonshotai/kimi-k3", "🌙"),
    ("Qwen/Qwen3.8-27B", "🌀"),
    ("qwen3-coder", "🌀"),
    ("custom-deployment", "🤖"),
    ("", "🤖"),
])
def test_model_family_icons(model, icon):
    assert model_icon(model) == icon


@pytest.mark.parametrize("model", [name for name, _ in MODEL_CATALOG])
def test_every_catalog_model_has_a_family_marker(model):
    assert model_icon(model) != "🤖"


def test_context_events_include_actual_model_and_marker():
    data = ContextStatus(1, 10, 0, 0, model="anthropic/claude-sonnet").event_data()
    assert data["model"] == "anthropic/claude-sonnet"
    assert data["model_icon"] == "✳︎"
