"""OpenAI Codex Responses provider using ChatGPT subscription OAuth."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Iterator

import httpx

from ene.utils.io import sanitize_unicode

from .auth import CredentialStore, OAuthCredential
from .openai_codex_oauth import (
    _decode_account_id,
    login_openai_codex,
    refresh_openai_codex,
)
from .responses import (
    ResponsesCompletionStream,
    build_responses_body,
    _prompt_cache_key,
)
from .registry import ProviderSettings
from .types import (
    AuthInteraction,
    CompletionRequest,
    CompletionResult,
    CompletionStream,
    LLMProvider,
    ProviderError,
)

CODEX_BASE_URL = "https://chatgpt.com/backend-api"
CODEX_RESPONSES_URL = f"{CODEX_BASE_URL}/codex/responses"
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})
_USAGE_LIMIT_CODES = frozenset({"usage_limit_reached", "usage_not_included", "rate_limit_exceeded"})


def _package_version() -> str:
    try:
        return version("ene")
    except PackageNotFoundError:
        return "dev"


def _build_body(request: CompletionRequest) -> dict[str, Any]:
    return build_responses_body(request, codex=True)


def _iter_sse(response: Any) -> Iterator[dict[str, Any]]:
    data_lines: list[str] = []
    for line in response.iter_lines():
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        if line == "":
            if data_lines:
                payload = "\n".join(data_lines).strip()
                data_lines.clear()
                if payload and payload != "[DONE]":
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError as e:
                        raise ProviderError("Invalid JSON in Codex event stream", retryable=False) from e
                    if not isinstance(event, dict):
                        raise ProviderError("Invalid Codex stream event", retryable=False)
                    yield event
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
    if data_lines:
        payload = "\n".join(data_lines).strip()
        if payload and payload != "[DONE]":
            try:
                event = json.loads(payload)
            except json.JSONDecodeError as e:
                raise ProviderError("Invalid JSON in Codex event stream", retryable=False) from e
            if not isinstance(event, dict):
                raise ProviderError("Invalid Codex stream event", retryable=False)
            yield event


class OpenAICodexProvider(LLMProvider):
    """ChatGPT subscription provider backed by OpenAI Codex Responses."""

    id = "openai-codex"

    def __init__(self, settings: ProviderSettings, store: CredentialStore | None = None):
        if settings.base_url:
            raise ValueError("openai-codex uses a fixed OpenAI endpoint; base_url is not allowed")
        if settings.api_key:
            raise ValueError("openai-codex uses OAuth; api_key is not allowed")
        self._store = store or CredentialStore()
        self._active_client: httpx.Client | None = None
        self._client_lock = threading.Lock()

    def _resolve_credential(self) -> OAuthCredential:
        try:
            credential = self._store.read_oauth(self.id)
        except Exception as e:
            raise ProviderError(f"Failed to read OpenAI Codex credentials: {e}", retryable=False) from e
        if credential is None:
            raise ProviderError(
                "OpenAI Codex is not logged in. Run /login openai-codex.",
                status_code=401,
                code="auth_required",
                retryable=False,
            )
        if credential.expires > time.time() + 60:
            return credential

        def refresh(current: OAuthCredential | None) -> OAuthCredential:
            if current is None:
                raise ProviderError("OpenAI Codex was logged out during refresh", retryable=False)
            if current.expires > time.time() + 60:
                return current
            try:
                return refresh_openai_codex(current)
            except ProviderError as e:
                raise ProviderError(
                    "OpenAI Codex OAuth refresh failed; run /login openai-codex again.",
                    status_code=e.status_code,
                    code="oauth_refresh_failed",
                    retryable=False,
                ) from e

        try:
            return self._store.modify_oauth(self.id, refresh)
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"Failed to update OpenAI Codex credentials: {e}", retryable=False) from e

    def _headers(self, credential: OAuthCredential) -> dict[str, str]:
        account_id = credential.metadata.get("account_id") or _decode_account_id(credential.access)
        return {
            "Authorization": f"Bearer {credential.access}",
            "chatgpt-account-id": account_id,
            "originator": "ene",
            "User-Agent": f"ene/{_package_version()}",
            "OpenAI-Beta": "responses=experimental",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }

    def _new_client(self, timeout: httpx.Timeout) -> httpx.Client:
        return httpx.Client(timeout=timeout)

    def _activate(self, client: httpx.Client) -> None:
        with self._client_lock:
            if self._active_client is not None:
                raise RuntimeError("Provider already has an active request")
            self._active_client = client

    def _release(self, client: httpx.Client) -> None:
        with self._client_lock:
            was_active = self._active_client is client
            if was_active:
                self._active_client = None
        if was_active:
            client.close()

    def open_stream(self, request: CompletionRequest) -> CompletionStream:
        credential = self._resolve_credential()
        timeout = httpx.Timeout(request.timeout or 600, connect=30)
        client = self._new_client(timeout)
        self._activate(client)
        try:
            headers = self._headers(credential)
            cache_key = _prompt_cache_key(request.session_id)
            if cache_key:
                headers["session-id"] = cache_key
                headers["x-client-request-id"] = cache_key
            outbound = client.build_request(
                "POST",
                CODEX_RESPONSES_URL,
                headers=headers,
                json=sanitize_unicode(_build_body(request)),
            )
            response = client.send(outbound, stream=True)
            if not response.is_success:
                response.read()
                error = self._http_error(response)
                response.close()
                raise error
        except BaseException:
            self._release(client)
            raise
        return ResponsesCompletionStream(
            _iter_sse(response), request.model, response.close,
            lambda: self._release(client),
        )

    def complete(self, request: CompletionRequest) -> CompletionResult:
        stream = self.open_stream(replace(request, stream=True))
        try:
            return stream.consume()
        finally:
            stream.close()

    def _http_error(self, response: httpx.Response) -> ProviderError:
        code = None
        message = response.reason_phrase or "OpenAI Codex request failed"
        reset = None
        try:
            payload = response.json()
            error = payload.get("error") if isinstance(payload, dict) else {}
            if isinstance(error, dict):
                code = error.get("code") or error.get("type")
                message = error.get("message") or message
                reset = error.get("resets_at")
        except (json.JSONDecodeError, TypeError):
            pass
        if code in _USAGE_LIMIT_CODES or response.status_code == 429:
            suffix = ""
            if isinstance(reset, (int, float)):
                minutes = max(0, round((reset - time.time()) / 60))
                suffix = f" Try again in about {minutes} minutes."
            return ProviderError(
                f"ChatGPT Codex usage limit reached.{suffix}",
                status_code=response.status_code,
                code=code,
                retryable=False,
            )
        return ProviderError(
            message,
            status_code=response.status_code,
            code=code,
            retryable=response.status_code in _RETRYABLE_STATUS,
        )

    def login(self, interaction: AuthInteraction) -> None:
        credential = login_openai_codex(interaction)
        if interaction.cancelled():
            raise ProviderError(
                "OpenAI Codex login cancelled", code="cancelled", retryable=False
            )
        self._store.write_oauth(self.id, credential)
        if interaction.cancelled():
            self._store.delete(self.id)
            raise ProviderError(
                "OpenAI Codex login cancelled", code="cancelled", retryable=False
            )

    def logout(self) -> None:
        self._store.delete(self.id)

    def auth_status(self) -> str:
        try:
            credential = self._store.read_oauth(self.id)
        except Exception as e:
            return f"credential error: {e}"
        if credential is None:
            return "not logged in"
        if credential.expires <= time.time():
            return "OAuth token expired (will refresh on use)"
        return "logged in with ChatGPT OAuth"

    def cancel(self) -> None:
        with self._client_lock:
            client = self._active_client
            self._active_client = None
        if client is not None:
            client.close()
