"""Model providers behind one interface."""

from __future__ import annotations

import os

from .base import Provider, ToolCall, Turn, Usage, price_for
from .openai_provider import DEFAULT_RPM, KNOWN_BASE_URLS, KNOWN_KEY_ENVS

__all__ = [
    "Provider",
    "ToolCall",
    "Turn",
    "Usage",
    "price_for",
    "make_provider",
    "DEFAULT_RPM",
    "KNOWN_BASE_URLS",
    "KNOWN_KEY_ENVS",
    "PROVIDERS",
]

PROVIDERS = ["anthropic", *KNOWN_BASE_URLS]


def make_provider(
    provider: str,
    model: str,
    effort: str,
    base_url: str | None = None,
    max_tokens: int | None = None,
    rpm: float | None = None,
) -> Provider:
    """Build a provider.

    Keys are read from the environment, never taken as arguments -- a key
    passed on the command line ends up in shell history and in the process
    list.
    """
    if provider == "anthropic":
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            model=model, effort=effort, base_url=base_url,
            max_tokens=max_tokens or 32000,
        )

    if provider not in KNOWN_BASE_URLS:
        raise ValueError(
            f"unknown provider {provider!r}; expected one of {', '.join(PROVIDERS)}"
        )

    from .openai_provider import OpenAICompatProvider

    key_env = KNOWN_KEY_ENVS[provider]
    api_key = os.environ.get(key_env)
    if not api_key:
        raise RuntimeError(
            f"{key_env} is not set. Export it in your shell:\n"
            f"    setx {key_env} \"...\"     (Windows, then restart the terminal)\n"
            f"    export {key_env}=...      (bash/zsh)"
        )

    return OpenAICompatProvider(
        model=model,
        effort=effort,
        api_key=api_key,
        base_url=base_url or KNOWN_BASE_URLS[provider],
        max_tokens=max_tokens or 16000,
        rpm=DEFAULT_RPM.get(provider) if rpm is None else (rpm or None),
    )
