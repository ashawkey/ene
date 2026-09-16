"""Shared Responses wire conversion and event handling for API-key and Codex providers."""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable

from ene.models import reasoning_kwargs, resolve_model_profile
from ene.messages import ContentPart, ImagePart, Message, RawPart, TextPart, ToolCall
from .types import CompletionRequest, CompletionResult, CompletionStream, ProviderError, ProviderUsage


def _user_content(content: str | list[ContentPart] | None) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    if not isinstance(content, list):
        raise ProviderError("Responses user message has invalid content", retryable=False)
    result: list[dict[str, Any]] = []
    for item in content:
        if isinstance(item, TextPart):
            result.append({"type": "input_text", "text": item.text})
        elif isinstance(item, ImagePart):
            image = item.image_url
            url = image.get("url") if isinstance(image, dict) else image
            if not isinstance(url, str):
                raise ProviderError("Responses image content has no URL", retryable=False)
            detail = image.get("detail", "auto") if isinstance(image, dict) else "auto"
            result.append({"type": "input_image", "detail": detail, "image_url": url})
        elif isinstance(item, RawPart):
            kind = item.raw.get("type") if isinstance(item.raw, dict) else "?"
            raise ProviderError(f"Unsupported Responses user content type: {kind!r}", retryable=False)
        else:
            raise ProviderError("Responses user content item is invalid", retryable=False)
    return result


def _call_id(value: Any) -> str:
    raw = str(value or "").split("|", 1)[0]
    normalized = re.sub(r"[^A-Za-z0-9_-]", "_", raw)[:64].rstrip("_")
    return normalized or "call_ene"


def _messages_to_input(
    messages: list[Message], model: str, state_key: str = "openai-codex",
) -> tuple[str, list[dict[str, Any]]]:
    instructions: list[str] = []
    items: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        if message.is_system:
            if message.text:
                instructions.append(message.text)
        elif message.is_user:
            content = _user_content(message.content)
            if content:
                items.append({"role": "user", "content": content})
        elif message.is_assistant:
            provider_state = message.provider_state
            response_state = provider_state.get(state_key) if isinstance(provider_state, dict) else None
            if (
                isinstance(response_state, dict)
                and response_state.get("model") == model
                and isinstance(response_state.get("output"), list)
            ):
                items.extend(response_state["output"])
                continue

            if message.text:
                items.append({
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": message.text, "annotations": []}],
                    "status": "completed",
                    "id": f"msg_ene_{index}",
                })
            for tool_call in message.tool_calls or []:
                items.append({
                    "type": "function_call",
                    "call_id": _call_id(tool_call.id),
                    "name": tool_call.name,
                    "arguments": tool_call.arguments or "{}",
                })
        elif message.is_tool:
            items.append({
                "type": "function_call_output",
                "call_id": _call_id(message.tool_call_id),
                "output": message.text or "(no tool output)",
            })
        else:
            raise ProviderError(f"Unsupported Responses message role: {message.role!r}", retryable=False)
    return "\n\n".join(instructions) or "You are a helpful assistant.", items


def _responses_tools(tools: list[dict[str, Any]], *, strict: bool | None = None) -> list[dict[str, Any]]:
    result = []
    for tool in tools:
        function = tool["function"]
        result.append({
            "type": "function",
            "name": function["name"],
            "description": function.get("description", ""),
            "parameters": function.get("parameters", {"type": "object", "properties": {}}),
            "strict": function.get("strict", strict),
        })
    return result


def _prompt_cache_key(session_id: str | None) -> str | None:
    if not session_id:
        return None
    value = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:64]
    return value or None


def build_responses_body(
    request: CompletionRequest, *, state_key: str = "openai-codex",
    codex: bool = False, reasoning_style: str | None = None,
) -> dict[str, Any]:
    instructions, items = _messages_to_input(request.messages, request.model, state_key)
    style = reasoning_style or resolve_model_profile(request.model).reasoning
    body: dict[str, Any] = {
        "model": request.model,
        "store": False,
        "stream": True if codex else request.stream,
        "instructions": instructions,
        "input": items,
        "text": (
            {
                "verbosity": "low",
                "format": {
                    "type": "json_schema",
                    "name": "batch_item",
                    "strict": True,
                    "schema": request.response_schema,
                },
            }
            if request.response_schema is not None
            else {"verbosity": "low"}
        ),
        "include": ["reasoning.encrypted_content"],
        "tool_choice": "auto",
        "parallel_tool_calls": True,
    }
    if request.tools:
        body["tools"] = _responses_tools(request.tools, strict=None if codex else False)
    if request.reasoning_effort is not None and (codex or style in {"openai", "openai-astra"}):
        effort = reasoning_kwargs(
            "openai-astra" if style == "openai-astra" else "openai",
            request.reasoning_effort,
        )["reasoning_effort"]
        body["reasoning"] = {
            "effort": effort,
            "summary": "auto",
        }
    if not codex and request.max_output_tokens is not None:
        body["max_output_tokens"] = request.max_output_tokens
    if not codex and style not in {"openai", "openai-astra"}:
        # Non-reasoning models need neither encrypted reasoning nor verbosity.
        body.pop("include")
        body["text"].pop("verbosity")
        if not body["text"]:
            body.pop("text")
    if not codex and not request.tools:
        body.pop("tool_choice")
        body.pop("parallel_tool_calls")
    cache_key = _prompt_cache_key(request.session_id)
    if cache_key:
        body["prompt_cache_key"] = cache_key
    return body


def _response_usage(response: dict[str, Any]) -> ProviderUsage | None:
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return None
    prompt = int(usage.get("input_tokens") or 0)
    completion = int(usage.get("output_tokens") or 0)
    input_details = usage.get("input_tokens_details")
    input_details = input_details if isinstance(input_details, dict) else {}
    output_details = usage.get("output_tokens_details")
    output_details = output_details if isinstance(output_details, dict) else {}
    return ProviderUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=int(usage.get("total_tokens") or prompt + completion),
        cached_prompt_tokens=int(input_details.get("cached_tokens") or 0),
        reasoning_tokens=int(output_details.get("reasoning_tokens") or 0),
    )


def _canonical_message(
    output: list[dict[str, Any]],
    model: str,
    streamed_text: str,
    streamed_reasoning: str,
    state_key: str = "openai-codex",
) -> Message:
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for item in output:
        kind = item.get("type")
        if kind == "message":
            for content in item.get("content") or []:
                if content.get("type") == "output_text":
                    text_parts.append(content.get("text") or "")
                elif content.get("type") == "refusal":
                    text_parts.append(content.get("refusal") or "")
        elif kind == "reasoning":
            blocks = item.get("summary") or item.get("content") or []
            reasoning_parts.extend(block.get("text") or "" for block in blocks)
        elif kind == "function_call":
            tool_calls.append(ToolCall(
                id=item.get("call_id") or item.get("id") or "",
                name=item.get("name") or "",
                arguments=item.get("arguments") or "{}",
            ))
    text = "".join(text_parts) or streamed_text
    reasoning = "\n\n".join(part for part in reasoning_parts if part) or streamed_reasoning
    return Message.assistant(
        content=text or None,
        tool_calls=tool_calls or None,
        provider_state={state_key: {"model": model, "output": output}},
        reasoning_content=reasoning or None,
    )


def response_result(
    response: dict[str, Any], model: str, state_key: str,
    *, fallback_output: list[dict[str, Any]] | None = None,
    streamed_text: str = "", streamed_reasoning: str = "",
) -> CompletionResult:
    if response.get("error") or response.get("status") in {"failed", "cancelled"}:
        error = response.get("error") or {}
        raise ProviderError(
            error.get("message") or "OpenAI Responses request failed",
            code=error.get("code"),
        )
    output = response.get("output")
    if not isinstance(output, list) or (not output and fallback_output):
        output = fallback_output or []
    message = _canonical_message(output, model, streamed_text, streamed_reasoning, state_key)
    if response.get("status") == "incomplete":
        reason = (response.get("incomplete_details") or {}).get("reason")
        finish_reason = "content_filter" if reason == "content_filter" else "length"
        # An incomplete item cannot be replayed as a completed response.
        message.provider_state = None
    elif message.tool_calls:
        finish_reason = "tool_calls"
    else:
        finish_reason = "stop"
    return CompletionResult(message, _response_usage(response), finish_reason)


class ResponsesCompletionStream(CompletionStream):
    def __init__(
        self, events: Iterable[dict[str, Any]], model: str,
        close_response: Callable[[], None], release: Callable[[], None],
        *, state_key: str = "openai-codex",
    ):
        self._events = events
        self._model = model
        self._close_response = close_response
        self._release = release
        self._state_key = state_key
        self._closed = False

    def consume(self, *, on_content=None, on_thinking=None, should_stop=None) -> CompletionResult:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        output_items: dict[int, dict[str, Any]] = {}
        terminal: dict[str, Any] | None = None

        for event in self._events:
            if should_stop is not None and should_stop():
                from ene.utils.interrupt import RequestInterrupted

                raise RequestInterrupted()
            kind = event.get("type")
            if kind == "response.output_item.added":
                item = event.get("item")
                if isinstance(item, dict):
                    output_items[int(event.get("output_index", len(output_items)))] = item
            elif kind in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
                delta = event.get("delta") or ""
                reasoning_parts.append(delta)
                if delta and on_thinking is not None:
                    on_thinking(delta)
            elif kind == "response.reasoning_summary_part.done":
                reasoning_parts.append("\n\n")
                if on_thinking is not None:
                    on_thinking("\n\n")
            elif kind in ("response.output_text.delta", "response.refusal.delta"):
                delta = event.get("delta") or ""
                text_parts.append(delta)
                if delta and on_content is not None:
                    on_content(delta)
            elif kind == "response.function_call_arguments.delta":
                index = int(event.get("output_index", 0))
                item = output_items.setdefault(index, {"type": "function_call", "arguments": ""})
                item["arguments"] = (item.get("arguments") or "") + (event.get("delta") or "")
            elif kind == "response.function_call_arguments.done":
                index = int(event.get("output_index", 0))
                item = output_items.setdefault(index, {"type": "function_call"})
                item["arguments"] = event.get("arguments") or item.get("arguments") or "{}"
            elif kind == "response.output_item.done":
                item = event.get("item")
                if isinstance(item, dict):
                    output_items[int(event.get("output_index", len(output_items)))] = item
            elif kind in ("response.completed", "response.done", "response.incomplete"):
                value = event.get("response")
                if isinstance(value, dict):
                    terminal = value
                break
            elif kind == "response.failed":
                response = event.get("response")
                response = response if isinstance(response, dict) else {}
                error = response.get("error")
                error = error if isinstance(error, dict) else {}
                raise ProviderError(
                    error.get("message") or "OpenAI Responses response failed",
                    code=error.get("code"),
                )
            elif kind == "error":
                error = event.get("error") if isinstance(event.get("error"), dict) else event
                raise ProviderError(
                    error.get("message") or "OpenAI Responses stream failed",
                    code=error.get("code"),
                )

        if should_stop is not None and should_stop():
            from ene.utils.interrupt import RequestInterrupted

            raise RequestInterrupted()
        if terminal is None:
            # Preserve a cleanly ended partial stream so the backend can append
            # it and continue from that exact point. Transport exceptions still
            # raise and use the normal retry path.
            output = [output_items[index] for index in sorted(output_items)]
            message = _canonical_message(
                output,
                self._model,
                "".join(text_parts),
                "".join(reasoning_parts),
                self._state_key,
            )
            # The accumulated output items may themselves be incomplete. Replay
            # the canonical partial text instead of treating this state as an
            # opaque completed Responses response.
            message.provider_state = None
            return CompletionResult(message, None, None)
        return response_result(
            terminal, self._model, self._state_key,
            fallback_output=[output_items[index] for index in sorted(output_items)],
            streamed_text="".join(text_parts),
            streamed_reasoning="".join(reasoning_parts),
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._close_response()
        finally:
            self._release()
