"""Provider-neutral shapes for one agent turn.

The verify loop, harness, sandbox, ledger and sweep do not care which model
wrote the patch -- only whether the tests pass afterwards. So the provider
boundary is drawn as narrowly as possible: build a request, parse a response,
report token usage. Everything else stays shared.

Each provider owns its own conversation history in its own native format,
because Anthropic and OpenAI disagree about where tool calls live (content
blocks versus a field on the message) and normalising that would mean
translating in both directions on every turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class Turn:
    """One assistant response, normalised."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = ""
    thinking: str = ""

    @property
    def is_noop(self) -> bool:
        """Did the model end its turn without asking for a single tool?

        This matters more than it looks. A turn with no tool calls means no
        edits were made, so re-running the suite is guaranteed to reproduce
        the same failures and the iteration is wasted. Some models do this
        routinely -- stopping early with a description of the fix instead of
        performing it -- so the loop has to detect and re-prompt rather than
        counting it as an attempt.
        """
        return not self.tool_calls


class Provider(Protocol):
    """One model conversation for one repo."""

    name: str

    def start(self, system: str, tools: list[dict[str, Any]]) -> None:
        """Set the system prompt and tool schemas. Called once per repo."""
        ...

    def send_user(self, text: str) -> None: ...

    def send_tool_results(self, results: list[tuple[str, str, bool]]) -> None:
        """Append tool outputs as (tool_call_id, content, is_error)."""
        ...

    def complete(self) -> Turn:
        """Call the model and append its reply to the conversation."""
        ...


class RateLimiter:
    """Proactive request spacing.

    Free tiers are tight enough that reacting to 429s is the wrong strategy:
    Gemini's free tier allows 5 requests/minute and answers a violation with
    a ~25 second penalty, so a burst of retries costs more wall time than
    simply never exceeding the limit. When the ceiling is known, space the
    requests instead of discovering it repeatedly.
    """

    def __init__(self, rpm: float | None):
        self.min_interval = 60.0 / rpm if rpm else 0.0
        self._last = 0.0

    def wait(self) -> float:
        """Block until the next request is allowed. Returns seconds slept."""
        if not self.min_interval:
            return 0.0
        import time

        delay = self.min_interval - (time.monotonic() - self._last)
        if delay > 0:
            time.sleep(delay)
        else:
            delay = 0.0
        self._last = time.monotonic()
        return delay


def retry_delay_from(error: Exception, default: float = 30.0) -> float:
    """Pull the server's suggested wait out of a 429 message.

    Providers state how long to wait ("Please retry in 25.357622168s"), and
    honouring that is both faster and politer than a fixed exponential ramp.
    """
    import re

    match = re.search(r"retry in ([0-9.]+)s", str(error))
    if match:
        try:
            return min(float(match.group(1)) + 1.0, 120.0)
        except ValueError:
            pass
    return default


def price_for(model: str, prices: dict[str, dict[str, float]]) -> dict[str, float] | None:
    """Look up per-MTok pricing, tolerating provider prefixes and suffixes.

    Model strings arrive in several shapes for the same model -- bare
    (`claude-opus-5`), routed (`deepseek/deepseek-v4-flash`), or dated
    (`gemini-2.0-flash-001`) -- and a miss here silently reports $0.00 for a
    run that actually cost money, which is worse than being wrong loudly.
    """
    if model in prices:
        return prices[model]
    bare = model.split("/")[-1]
    if bare in prices:
        return prices[bare]
    for known, price in prices.items():
        if bare.startswith(known) or known.startswith(bare):
            return price
    return None
