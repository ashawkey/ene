"""Resolve session model options without modifying user configuration."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit

from ene.config import CONFIG_PATH
from ene.models import resolve_model_alias, resolve_model_profile
from ene.providers import provider_names


def resolve_session_model(options: dict[str, Any], config: dict) -> dict[str, Any]:
    """Return LLMAgent model arguments for an alias or explicit temporary endpoint."""
    model = options.get("model", "")
    base_url = options.get("base_url")
    if base_url is not None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError("--base-url must be an HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("--base-url must not contain credentials; use --api-key-env")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("--base-url requires --model with the server's model ID")
        api_key = options.get("api_key")
        key_env = options.get("api_key_env")
        if api_key is not None and key_env is not None:
            raise ValueError("--api-key and --api-key-env are mutually exclusive")
        if key_env is not None:
            api_key = os.environ.get(key_env)
            if not api_key:
                raise ValueError(f"API key environment variable '{key_env}' is unset or empty")
        model_conf = {
            "model": model,
            "base_url": base_url,
            # The OpenAI SDK requires a non-empty key even for unauthenticated servers.
            "api_key": api_key or "EMPTY",
            "api": options.get("api") or "chat_completions",
        }
        alias = ""
    else:
        for option in ("api_key", "api_key_env", "api"):
            if options.get(option) is not None:
                raise ValueError(f"--{option.replace('_', '-')} requires --base-url")
        models = config.get("openai", {})
        if not isinstance(models, dict) or not models:
            raise ValueError(f"No models found in config: {CONFIG_PATH}")
        alias = resolve_model_alias(model or next(iter(models)), models)
        model_conf = models[alias]
        if not isinstance(model_conf, dict):
            raise ValueError(f"Model '{alias}' not found in config: {CONFIG_PATH}")

    provider = model_conf.get("provider", "openai")
    if provider not in provider_names():
        raise ValueError(f"Unknown provider '{provider}'. Available: {', '.join(provider_names())}")
    api = model_conf.get("api", "chat_completions")
    if api not in ("chat_completions", "responses"):
        raise ValueError("api must be 'chat_completions' or 'responses'")
    profile = options.get("model_profile")
    if profile is not None:
        resolve_model_profile(model_conf.get("model", alias), profile)
    result = {
        "model": model_conf.get("model", alias),
        "model_alias": alias,
        "provider_name": provider,
        "api_key": model_conf.get("api_key", ""),
        "base_url": model_conf.get("base_url", ""),
        "api": api,
        "model_profile": profile,
        "reasoning_effort": options.get("reasoning_effort") or model_conf.get("reasoning_effort", "high"),
    }
    for field in ("context_length", "max_output_tokens"):
        override = options.get(field)
        if override is not None and (not isinstance(override, int) or isinstance(override, bool) or override <= 0):
            raise ValueError(f"--{field.replace('_', '-')} must be a positive integer")
        result[field] = override if override is not None else model_conf.get(field)
    return result
