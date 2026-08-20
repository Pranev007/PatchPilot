"""Anthropic provider.

Keeps the Anthropic-specific features the OpenAI-compatible path cannot
express: adaptive thinking, reasoning effort, explicit prompt-cache
breakpoints, and server-side model fallbacks.
"""

from __future__ import annotations

from typing import Any

import anthropic

from .base import ToolCall, Turn, Usage


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, effort: str, api_key: str | None = None,
                 base_url: str | None = None, max_tokens: int = 32000):
        kwargs: dict[str, Any] = {"max_retries": 5}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self._client_kwargs = kwargs
        self._client: anthropic.Anthropic | None = None
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.messages: list[dict[str, Any]] = []
        self._system: list[dict[str, Any]] = []
        self._tools: list[dict[str, Any]] = []
        self._fallbacks_supported = True

    @property
    def client(self) -> anthropic.Anthropic:
        # Built on first use so a baseline-only run needs no credentials.
        if self._client is None:
            self._client = anthropic.Anthropic(**self._client_kwargs)
        return self._client

    def start(self, system: str, tools: list[dict[str, Any]]) -> None:
        self._system = [
            {
                "type": "text",
                "text": system,
                # Stable prefix: system + tools render ahead of messages, so
                # one breakpoint here caches both.
                "cache_control": {"type": "ephemeral"},
            }
        ]
        self._tools = tools

    def send_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def send_tool_results(self, results: list[tuple[str, str, bool]]) -> None:
        # All results for one assistant turn go back in a single user message
        # -- splitting them trains the model out of parallel tool calls.
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": content or "(empty)",
                        "is_error": is_error,
                    }
                    for call_id, content, is_error in results
                ],
            }
        )

    def complete(self) -> Turn:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": self._system,
            "tools": self._tools,
            "messages": self.messages,
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": self.effort},
            # Rolling breakpoint on the last cacheable block, so each turn
            # reuses the whole conversation prefix.
            "cache_control": {"type": "ephemeral"},
        }

        response = None
        if self._fallbacks_supported:
            try:
                with self.client.beta.messages.stream(
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                    **kwargs,
                ) as stream:
                    response = stream.get_final_message()
            except (TypeError, anthropic.BadRequestError) as exc:
                if "fallback" not in str(exc).lower():
                    raise
                self._fallbacks_supported = False
        if response is None:
            with self.client.messages.stream(**kwargs) as stream:
                response = stream.get_final_message()

        turn = Turn(stop_reason=response.stop_reason or "")
        if response.stop_reason == "refusal":
            turn.stop_reason = "refusal"
            return turn

        self.messages.append({"role": "assistant", "content": response.content})

        texts = []
        for block in response.content:
            if block.type == "text":
                texts.append(block.text)
            elif block.type == "thinking" and block.thinking:
                turn.thinking += block.thinking
            elif block.type == "tool_use":
                turn.tool_calls.append(
                    ToolCall(id=block.id, name=block.name, args=dict(block.input))
                )
        turn.text = "\n".join(texts)

        u = response.usage
        turn.usage = Usage(
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )
        return turn
