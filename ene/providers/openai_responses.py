"""OpenAI-compatible Responses API provider using API-key authentication."""

from __future__ import annotations

from typing import Any

from ene.utils.io import sanitize_unicode

from .openai_compatible import OpenAICompatibleProvider
from .responses import ResponsesCompletionStream, build_responses_body, response_result
from .types import CompletionRequest, CompletionResult, CompletionStream


class OpenAIResponsesProvider(OpenAICompatibleProvider):
    """Reuse API-key client lifecycle while speaking the Responses protocol."""

    id = "openai"
    _state_key = "openai-responses"

    def _kwargs(self, request: CompletionRequest) -> dict[str, Any]:
        kwargs = build_responses_body(
            request, state_key=self._state_key, reasoning_style=self._reasoning_style,
        )
        # Older SDKs support Responses but predate this optional wire field.
        cache_key = kwargs.pop("prompt_cache_key", None)
        if cache_key is not None:
            kwargs["extra_body"] = {"prompt_cache_key": cache_key}
        if request.timeout is not None:
            kwargs["timeout"] = request.timeout
        return sanitize_unicode(kwargs)

    def complete(self, request: CompletionRequest) -> CompletionResult:
        if request.stream:
            raise ValueError("complete() requires stream=False")
        client = self._new_client()
        self._activate(client)
        try:
            response = client.responses.create(**self._kwargs(request))
            # Replay API field names without introducing SDK-only defaults.
            return response_result(
                response.model_dump(by_alias=True, exclude_unset=True),
                request.model, self._state_key,
            )
        finally:
            self._release(client)

    def open_stream(self, request: CompletionRequest) -> CompletionStream:
        if not request.stream:
            raise ValueError("open_stream() requires stream=True")
        client = self._new_client()
        self._activate(client)
        try:
            raw_stream = client.responses.create(**self._kwargs(request))
        except BaseException:
            self._release(client)
            raise
        return ResponsesCompletionStream(
            (event.model_dump(by_alias=True, exclude_unset=True) for event in raw_stream),
            request.model, raw_stream.close, lambda: self._release(client),
            state_key=self._state_key,
        )
