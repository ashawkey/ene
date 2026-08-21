"""Model capabilities and provider-specific reasoning configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]
REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class ModelProfile:
    """Properties of a model family that affect API behaviour."""

    context_length: int = 128_000
    # "openai" | "anthropic" | "gemini" | "deepseek" | "deepseek-v4" | "glm" | "glm-5" | "kimi" | "qwen3.8"
    reasoning: str | None = None
    supports_image_input: bool = False
    # Max output tokens per request. Reasoning tokens count against this budget,
    # so reasoning models need generous ceilings to avoid mid-tool-call
    # `finish_reason="length"` truncation. Kept below each provider's hard cap.
    max_output_tokens: int = 32_000


# Ordered most-specific → least-specific within each family.
# Matching is case-insensitive substring; first hit wins.
MODEL_CATALOG: list[tuple[str, ModelProfile]] = [
    ("gpt-5", ModelProfile(context_length=258_000, reasoning="openai", supports_image_input=True, max_output_tokens=128_000)),
    ("gpt", ModelProfile(supports_image_input=True)),
    ("gemini", ModelProfile(context_length=1_000_000, reasoning="gemini", supports_image_input=True, max_output_tokens=64_000)),
    ("claude", ModelProfile(context_length=1_000_000, reasoning="anthropic", supports_image_input=True, max_output_tokens=64_000)),
    # deepseek-v4-flash/pro gained three-tier thinking effort (low/high/max);
    # the -vision-exp variant is the same model family with image input;
    # older deepseek models keep the legacy two-tier mapping.
    ("deepseek-v4-flash-vision-exp", ModelProfile(context_length=1_000_000, reasoning="deepseek-v4", supports_image_input=True, max_output_tokens=64_000)),
    ("deepseek-v4", ModelProfile(context_length=1_000_000, reasoning="deepseek-v4", max_output_tokens=64_000)),
    ("deepseek", ModelProfile(context_length=1_000_000, reasoning="deepseek", max_output_tokens=64_000)),
    # GLM-5.3 supports low/high/max; GLM-5.2 (and older) accept only max/high.
    ("glm-5.3", ModelProfile(context_length=1_000_000, reasoning="glm-5", max_output_tokens=64_000)),
    ("glm-5", ModelProfile(context_length=1_000_000, reasoning="glm", max_output_tokens=64_000)),
    ("glm", ModelProfile(context_length=1_000_000, reasoning="glm", max_output_tokens=64_000)),
    ("kimi-k3", ModelProfile(context_length=1_000_000, reasoning="kimi", supports_image_input=True, max_output_tokens=64_000)),
    ("kimi", ModelProfile(supports_image_input=True)),
    # Qwen3.8 (hybrid-attention family): the 27B dense is multimodal and has
    # 262K native context; the 2.4T MoE flagship is text-only.
    ("qwen3.8-27b", ModelProfile(context_length=262_144, reasoning="qwen3.8", supports_image_input=True, max_output_tokens=64_000)),
    ("qwen3.8", ModelProfile(context_length=262_144, reasoning="qwen3.8", max_output_tokens=64_000)),
]

DEFAULT_PROFILE = ModelProfile()


def resolve_model_profile(model_id: str, model_alias: str = "") -> ModelProfile:
    """Resolve a model ID, falling back to its configured alias."""
    for candidate in (model_id, model_alias):
        lower = candidate.lower()
        for pattern, profile in MODEL_CATALOG:
            if pattern in lower:
                return profile
    return DEFAULT_PROFILE


def reasoning_kwargs(style: str | None, effort: ReasoningEffort) -> dict[str, Any]:
    """Translate normalized reasoning effort to OpenAI-compatible API fields."""
    if style is None:
        return {}
    if style == "openai":
        # OpenAI's highest reasoning level is named xhigh.
        return {"reasoning_effort": "xhigh" if effort == "max" else effort}
    if style == "anthropic":
        if effort == "none":
            return {"extra_body": {"thinking": {"type": "disabled"}}}
        # Adaptive-thinking models use adaptive mode by default. Omitting the
        # explicit thinking field also avoids OpenAI-compatible gateways that
        # incorrectly translate it back to the unsupported legacy "enabled"
        # mode. Anthropic has no minimal level, and names its top level max.
        mapped = {"minimal": "low", "xhigh": "max"}.get(effort, effort)
        return {"extra_body": {"output_config": {"effort": mapped}}}
    if style == "gemini":
        # Gemini 3 uses qualitative thinking levels; it has no xhigh/max level.
        mapped = {"none": "minimal", "xhigh": "high", "max": "high"}.get(effort, effort)
        return {
            "extra_body": {
                "google": {
                    "thinking_config": {
                        "thinking_level": mapped,
                        "include_thoughts": True,
                    }
                }
            }
        }
    if style in ("deepseek", "glm"):
        # Legacy two-tier thinking effort for older DeepSeek/GLM models: only
        # high and max are honored; anything lower collapses to high.
        if effort == "none":
            return {"extra_body": {"thinking": {"type": "disabled"}}}
        mapped = "max" if effort in ("xhigh", "max") else "high"
        return {
            "reasoning_effort": mapped,
            "extra_body": {"thinking": {"type": "enabled"}},
        }
    if style in ("deepseek-v4", "glm-5"):
        # Three-tier thinking effort (low/high/max) for deepseek-v4-flash/pro
        # and GLM-5.3. DeepSeek's official mapping table (identical for
        # v4-flash and v4-pro): low→low, medium→high, high→high, xhigh→high,
        # max→max. GLM-5.3 accepts max/high/low, so xhigh maps to its top tier.
        if effort == "none":
            return {"extra_body": {"thinking": {"type": "disabled"}}}
        mapped = {
            "deepseek-v4": {"minimal": "low", "medium": "high", "xhigh": "high", "max": "max"},
            "glm-5": {"minimal": "low", "medium": "high", "xhigh": "max", "max": "max"},
        }[style].get(effort, effort)
        return {
            "reasoning_effort": mapped,
            "extra_body": {"thinking": {"type": "enabled"}},
        }
    if style == "kimi":
        # Kimi K3 always reasons (thinking cannot be disabled) and currently
        # only accepts reasoning_effort="max"; all effort levels map to it.
        return {"reasoning_effort": "max"}
    if style == "qwen3.8":
        # Qwen3.8 controls thinking via chat_template_kwargs (vLLM/Transformers
        # convention): thinking is on by default, disabled with enable_thinking,
        # and tuned adaptively with reasoning_effort (low | medium | xhigh).
        if effort == "none":
            return {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
        mapped = {"minimal": "low", "high": "xhigh", "xhigh": "xhigh", "max": "xhigh"}.get(effort, effort)
        return {"extra_body": {"chat_template_kwargs": {"reasoning_effort": mapped}}}
    raise ValueError(f"Unknown reasoning style: {style}")
