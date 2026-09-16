"""LLM provider interfaces and built-in provider registry."""

from .openai_codex import OpenAICodexProvider
from .openai_compatible import OpenAICompatibleProvider
from .openai_responses import OpenAIResponsesProvider
from .registry import (
    ProviderSettings,
    create_provider,
    provider_names,
    register_provider,
)
from .types import (
    AuthInteraction,
    CompletionRequest,
    CompletionResult,
    CompletionStream,
    LLMProvider,
    ProviderError,
    ProviderUsage,
)

def _openai_provider(settings: ProviderSettings) -> LLMProvider:
    if settings.api == "responses":
        return OpenAIResponsesProvider(settings)
    return OpenAICompatibleProvider(settings)


register_provider("openai", _openai_provider)
register_provider("openai-codex", OpenAICodexProvider)

__all__ = [
    "AuthInteraction",
    "CompletionRequest",
    "CompletionResult",
    "CompletionStream",
    "LLMProvider",
    "OpenAICodexProvider",
    "OpenAICompatibleProvider",
    "OpenAIResponsesProvider",
    "ProviderError",
    "ProviderSettings",
    "ProviderUsage",
    "create_provider",
    "provider_names",
    "register_provider",
]
