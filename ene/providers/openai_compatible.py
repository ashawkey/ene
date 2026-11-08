"""OpenAI-compatible Chat Completions provider."""

from __future__ import annotations

import threading
from typing import Any, Callable

from ene.models import reasoning_kwargs
from ene.utils.io import sanitize_unicode
from ene.utils.streaming import consume_stream, message_from_sdk

from .registry import ProviderSettings
from .types import (
    CompletionRequest,
    CompletionResult,
    CompletionStream,
    LLMProvider,
    ProviderUsage,
)


def _detail_tokens(details: Any, name: str) -> int:
    if details is None:
        return 0
    return int(getattr(details, name, 0) or 0)


def normalize_usage(usage: Any) -> ProviderUsage | None:
    """Normalize an OpenAI SDK usage object."""
    if usage is None:
        return None
    prompt = int(usage.prompt_tokens or 0)
    completion = int(usage.completion_tokens or 0)
    return ProviderUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=int(usage.total_tokens or prompt + completion),
        cached_prompt_tokens=_detail_tokens(
            getattr(usage, "prompt_tokens_details", None), "cached_tokens"
        ),
        reasoning_tokens=_detail_tokens(
            getattr(usage, "completion_tokens_details", None), "reasoning_tokens"
        ),
    )


class _OpenAICompletionStream(CompletionStream):
    def __init__(self, raw_stream: Any, release: Callable[[], None]):
        self._raw_stream = raw_stream
        self._release = release
        self._closed = False

    def consume(self, *, on_content=None, on_thinking=None, should_stop=None) -> CompletionResult:
        message, usage, finish_reason = consume_stream(
            self._raw_stream,
            on_content=on_content,
            on_thinking=on_thinking,
            should_stop=should_stop,
        )
        return CompletionResult(message, normalize_usage(usage), finish_reason)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._raw_stream.close()
        finally:
            self._release()


class OpenAICompatibleProvider(LLMProvider):
    """Adapter for servers implementing OpenAI Chat Completions."""

    id = "openai"

    def __init__(self, settings: ProviderSettings):
        self._api_key = settings.api_key
        self._base_url = settings.base_url
        self._reasoning_style = settings.reasoning_style
        self._active_client: Any = None
        # One long-lived client per provider keeps its HTTP connection pool, so
        # each round skips a new TCP and TLS handshake. Only cancel() closes it.
        self._client: Any = None
        self._client_lock = threading.Lock()

    def _new_client(self):
        from openai import OpenAI

        return OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
            max_retries=0,
        )

    def _acquire(self) -> Any:
        with self._client_lock:
            if self._active_client is not None:
                raise RuntimeError("Provider already has an active request")
            if self._client is None:
                self._client = self._new_client()
            self._active_client = self._client
            return self._client

    def _release(self, client: Any) -> None:
        # The client stays open for reuse. cancel() already closed and
        # discarded it if the request was interrupted.
        with self._client_lock:
            if self._active_client is client:
                self._active_client = None

    def context_length(self, model: str) -> int | None:
        from openai import APIError

        client = self._acquire()
        try:
            # /models is optional on compatible gateways. A failed discovery
            # must not prevent an otherwise supported completion request.
            try:
                models = getattr(client.models.list(timeout=5), "data", None)
            except APIError:
                return None
            if not isinstance(models, list):
                return None
            for entry in models:
                if getattr(entry, "id", None) == model:
                    limit = getattr(entry, "max_model_len", None)
                    return limit if type(limit) is int and limit > 0 else None
            return None
        finally:
            self._release(client)

    def _kwargs(self, request: CompletionRequest) -> dict[str, Any]:
        # Serialize at the wire boundary: Messages become OpenAI wire dicts,
        # minus the keys this API does not accept.
        messages = [
            {
                key: value
                for key, value in message.to_wire().items()
                if key not in {"provider_state", "display_content", "origin"}
            }
            for message in request.messages
        ]
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": sanitize_unicode(messages),
            "stream": request.stream,
        }
        if request.max_output_tokens is not None:
            kwargs["max_tokens"] = request.max_output_tokens
        if request.stream:
            kwargs["stream_options"] = {"include_usage": True}
        if request.tools:
            kwargs["tools"] = request.tools
        if request.response_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "batch_item",
                    "strict": True,
                    "schema": request.response_schema,
                },
            }
        if request.reasoning_effort is not None:
            kwargs.update(reasoning_kwargs(self._reasoning_style, request.reasoning_effort))
        if request.timeout is not None:
            kwargs["timeout"] = request.timeout
        return kwargs

    def complete(self, request: CompletionRequest) -> CompletionResult:
        if request.stream:
            raise ValueError("complete() requires stream=False")
        client = self._acquire()
        try:
            response = client.chat.completions.create(**self._kwargs(request))
            choice = response.choices[0]
            return CompletionResult(
                message=message_from_sdk(choice.message),
                usage=normalize_usage(response.usage),
                finish_reason=choice.finish_reason,
            )
        finally:
            self._release(client)

    def open_stream(self, request: CompletionRequest) -> CompletionStream:
        if not request.stream:
            raise ValueError("open_stream() requires stream=True")
        client = self._acquire()
        try:
            raw_stream = client.chat.completions.create(**self._kwargs(request))
        except BaseException:
            self._release(client)
            raise
        return _OpenAICompletionStream(raw_stream, lambda: self._release(client))

    def cancel(self) -> None:
        # Closing the client is what interrupts a blocked request; the next
        # request then builds a fresh one.
        with self._client_lock:
            client = self._active_client
            self._active_client = None
            if client is not None and client is self._client:
                self._client = None
        if client is not None:
            client.close()

    def auth_status(self) -> str:
        return "API key configured" if self._api_key else "API key missing"
