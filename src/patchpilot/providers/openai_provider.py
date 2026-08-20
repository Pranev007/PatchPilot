"""OpenAI-compatible provider.

One implementation covers every endpoint that speaks the Chat Completions
protocol, which is most of them:

    Gemini      https://generativelanguage.googleapis.com/v1beta/openai/
    DeepSeek    https://api.deepseek.com/v1
    OpenRouter  https://openrouter.ai/api/v1
    Together    https://api.together.xyz/v1
    HuggingFace https://router.huggingface.co/v1
    OpenAI      (default)

Differences from the Anthropic path that matter:

  * Tool schemas are wrapped in {"type": "function", "function": {...}} and
    the JSON Schema lives under `parameters` rather than `input_schema`.
  * Tool calls arrive as a field on the assistant message, not as content
    blocks, and results go back as separate role="tool" messages -- one per
    call, not batched.
  * There is no explicit cache control. Providers that cache do it
    automatically and report it under prompt_tokens_details.
  * `reasoning_effort` is accepted by some models and rejected by others, so
    it is sent optimistically and dropped permanently on first rejection.
"""

from __future__ import annotations

import json
from typing import Any

from .base import RateLimiter, ToolCall, Turn, Usage, retry_delay_from

# Endpoints worth having a shorthand for. Anything else can be passed with
# --base-url directly.
KNOWN_BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "deepseek": "https://api.deepseek.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "together": "https://api.together.xyz/v1",
    "huggingface": "https://router.huggingface.co/v1",
    "openai": None,  # SDK default
}

# Conservative default requests-per-minute per provider, chosen for the free
# tier because that is what a first run hits. Gemini's free tier allows 5/min
# on the newer Flash models and answers a violation with a ~25s penalty, so
# spacing requests is cheaper than discovering the ceiling repeatedly.
# Override with --rpm; 0 disables spacing entirely.
DEFAULT_RPM = {
    "gemini": 5,
    "huggingface": 10,
    "openrouter": 20,
}

# Environment variable each shorthand reads its key from, so a key never has
# to be passed on the command line where it would land in shell history.
KNOWN_KEY_ENVS = {
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "together": "TOGETHER_API_KEY",
    "huggingface": "HF_TOKEN",
    "openai": "OPENAI_API_KEY",
}


def to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Anthropic-style tool defs to OpenAI function defs."""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


def _echo_tool_call(call: Any) -> dict[str, Any]:
    """Rebuild an assistant tool call for the conversation history.

    Provider extension fields are preserved rather than dropped. This is not
    cosmetic: Gemini 3.x attaches a `thought_signature` under
    `extra_content.google` and *rejects the next request* if it is missing --

        Function call is missing a thought_signature in functionCall parts.
        This is required for tools to work correctly.

    so a compat layer that reconstructs only the fields it recognises breaks
    multi-turn tool use entirely. Copying the whole extension blob keeps this
    working for whatever the next provider decides to attach.
    """
    entry: dict[str, Any] = {
        "id": call.id,
        "type": "function",
        "function": {
            "name": call.function.name,
            "arguments": call.function.arguments,
        },
    }
    extra = (getattr(call, "model_extra", None) or {}).get("extra_content")
    if extra:
        entry["extra_content"] = extra
    return entry


class OpenAICompatProvider:
    name = "openai-compatible"

    def __init__(
        self,
        model: str,
        effort: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        max_tokens: int = 16000,
        rpm: float | None = None,
    ):
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "The openai package is required for non-Anthropic providers. "
                "Install it with: uv pip install 'patchpilot[openai]'"
            ) from exc

        kwargs: dict[str, Any] = {"max_retries": 5}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        self._openai = openai
        self._client_kwargs = kwargs
        self._client = None
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.messages: list[dict[str, Any]] = []
        self._tools: list[dict[str, Any]] = []
        self._effort_supported = effort is not None
        self.limiter = RateLimiter(rpm)
        self.rate_limit_waits = 0.0

    @property
    def client(self):
        if self._client is None:
            self._client = self._openai.OpenAI(**self._client_kwargs)
        return self._client

    def start(self, system: str, tools: list[dict[str, Any]]) -> None:
        self.messages = [{"role": "system", "content": system}]
        self._tools = to_openai_tools(tools)

    def send_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def send_tool_results(self, results: list[tuple[str, str, bool]]) -> None:
        # One message per result. Unlike Anthropic there is no is_error flag,
        # so the error is marked inline -- without it the model cannot tell a
        # failed edit from a successful one that printed a warning.
        for call_id, content, is_error in results:
            body = content or "(empty)"
            if is_error:
                body = f"ERROR: {body}"
            self.messages.append(
                {"role": "tool", "tool_call_id": call_id, "content": body}
            )

    MAX_429_RETRIES = 6

    def _request(self) -> Any:
        for attempt in range(self.MAX_429_RETRIES + 1):
            self.limiter.wait()
            try:
                return self._request_once()
            except self._openai.RateLimitError as exc:
                if attempt == self.MAX_429_RETRIES:
                    raise
                import time

                delay = retry_delay_from(exc)
                self.rate_limit_waits += delay
                time.sleep(delay)
        raise RuntimeError("unreachable")

    def _request_once(self) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self.messages,
            "tools": self._tools,
            "max_completion_tokens": self.max_tokens,
        }
        if self._effort_supported:
            kwargs["reasoning_effort"] = self.effort
        try:
            return self.client.chat.completions.create(**kwargs)
        except self._openai.BadRequestError as exc:
            text = str(exc).lower()
            retryable = ("reasoning_effort", "max_completion_tokens")
            if not any(k in text for k in retryable):
                raise
            # Endpoint does not know one of the newer parameters. Drop it and
            # retry once rather than failing a run that is otherwise fine.
            self._effort_supported = False
            kwargs.pop("reasoning_effort", None)
            if "max_completion_tokens" in text:
                kwargs["max_tokens"] = kwargs.pop("max_completion_tokens")
            return self.client.chat.completions.create(**kwargs)

    def complete(self) -> Turn:
        response = self._request()
        choice = response.choices[0]
        message = choice.message
        turn = Turn(stop_reason=choice.finish_reason or "")

        if choice.finish_reason == "content_filter":
            turn.stop_reason = "refusal"
            return turn

        raw_calls = message.tool_calls or []
        entry: dict[str, Any] = {
            "role": "assistant",
            "content": message.content or "",
        }
        if raw_calls:
            entry["tool_calls"] = [_echo_tool_call(c) for c in raw_calls]
        self.messages.append(entry)

        turn.text = message.content or ""
        turn.thinking = getattr(message, "reasoning_content", "") or ""

        for call in raw_calls:
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                # Malformed arguments are a model failure, not a crash. Pass
                # them through so the dispatcher reports the error back and
                # the model gets a chance to correct itself.
                args = {"__malformed__": call.function.arguments}
            turn.tool_calls.append(
                ToolCall(id=call.id, name=call.function.name, args=args)
            )

        u = getattr(response, "usage", None)
        if u is not None:
            cached = 0
            details = getattr(u, "prompt_tokens_details", None)
            if details is not None:
                cached = getattr(details, "cached_tokens", 0) or 0
            turn.usage = Usage(
                # Uncached input only, so the cost maths matches the Anthropic
                # path where these two are already disjoint.
                input_tokens=max((getattr(u, "prompt_tokens", 0) or 0) - cached, 0),
                output_tokens=getattr(u, "completion_tokens", 0) or 0,
                cache_read_tokens=cached,
            )
        return turn
