"""Direct, context-free model completions used by the bundled batch skill."""

from __future__ import annotations

import json
from typing import Any

from ene.messages import ImagePart, Message, TextPart
from ene.models import REASONING_EFFORTS, ReasoningEffort
from ene.providers import CompletionRequest, ProviderUsage, create_provider
from ene.utils.interrupt import RequestInterrupted


class BatchCompletionMixin:
    """Run tool-free model calls without exposing provider credentials to skills."""

    def run_batch_completion(
        self,
        instruction: str,
        item: str,
        *,
        image_url: str | None = None,
        output_schema: dict[str, Any] | None = None,
        max_output_tokens: int | None = None,
        reasoning_effort: ReasoningEffort = "low",
    ) -> dict[str, Any]:
        """Complete one independent item with a fresh provider instance."""
        if reasoning_effort not in REASONING_EFFORTS:
            raise ValueError(f"Invalid reasoning_effort: {reasoning_effort}")
        if image_url is not None and not self.profile.supports_image_input:
            raise ValueError(f"Model '{self.model}' does not support image input")
        if self.cancellation is not None and self.cancellation.cancelled:
            raise RequestInterrupted()

        content: str | list[TextPart | ImagePart]
        if image_url is None:
            content = item
        else:
            content = [TextPart(f"Item: {item}"), ImagePart({"url": image_url})]
        request = CompletionRequest(
            model=self.model,
            messages=[Message.system(instruction), Message.user(content)],
            stream=False,
            max_output_tokens=max_output_tokens or self.max_output_tokens,
            reasoning_effort=reasoning_effort,
            response_schema=output_schema,
            session_id=f"{self._session_id}-batch" if self._session_id else None,
        )
        provider = create_provider(self.provider_name, self._provider_settings)
        with self._batch_provider_lock:
            self._batch_providers.add(provider)
        try:
            result = provider.complete(request)
        finally:
            with self._batch_provider_lock:
                self._batch_providers.discard(provider)
            provider.close()

        if self.cancellation is not None and self.cancellation.cancelled:
            raise RequestInterrupted()
        text = result.message.text
        if not text:
            raise RuntimeError("The model returned no text")
        value: Any = text
        if output_schema is not None:
            try:
                value = json.loads(text)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"The model returned invalid JSON: {e}") from e
        usage = result.usage
        if usage is not None:
            self._accumulate_usage(usage)
        return {
            "result": value,
            "usage": _usage_dict(usage),
        }

    def cancel_batch_completions(self) -> None:
        """Cancel every direct completion currently owned by a batch."""
        with self._batch_provider_lock:
            providers = list(self._batch_providers)
        for provider in providers:
            try:
                provider.cancel()
            except Exception:
                pass


def _usage_dict(usage: ProviderUsage | None) -> dict[str, int] | None:
    if usage is None:
        return None
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "cached_prompt_tokens": usage.cached_prompt_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
    }
