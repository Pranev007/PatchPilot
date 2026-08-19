"""The agent conversation.

A hand-written tool-use loop rather than the SDK tool runner. Three reasons,
all of which are worth being able to explain:

  1. Tools are bound to a specific sandbox instance, so they cannot be
     module-level decorated functions.
  2. Every tool call is recorded in the ledger -- the trace file is what the
     failure taxonomy in the README is built from.
  3. The spend cap has to be enforced between turns, not after the fact.

The outer verify loop (run tests -> feed regressions back) lives in runner.py.
That split is the project: the agent proposes, the test suite disposes.
"""

from __future__ import annotations

from typing import Any, Callable

import anthropic

from .config import RunConfig
from .ledger import Ledger
from .sandbox import Sandbox
from .tools import TOOL_DEFS, build_dispatch, tool_arg_preview

SYSTEM_PROMPT = """\
You are migrating a Python package from an old interpreter to a new one, in \
place, inside a sandboxed checkout.

Scope. Change only what the interpreter upgrade requires:

  - stdlib modules removed or deprecated between the two versions \
(distutils, imp, asynchat, asyncore, cgi, telnetlib, and similar)
  - APIs deprecated in favour of a replacement (datetime.utcnow, \
asyncio.get_event_loop in non-async context, locale.getdefaultlocale, \
pkg_resources, unittest method aliases such as assertEquals)
  - packaging metadata that pins or caps the old version (setup.py, \
setup.cfg, pyproject.toml, tox.ini, CI matrices)
  - dependency versions that do not have a wheel for, or are incompatible \
with, the new interpreter

Do not refactor, restyle, add features, add type annotations, or "improve" \
code that already works on the new interpreter. Do not rewrite tests to make \
them pass -- if a test fails, the fix belongs in the code under test, not in \
the assertion. If you conclude a test itself is wrong, say so plainly and \
leave it.

Method. Read before you edit. Prefer edit_file over write_file. Make the \
smallest change that resolves the incompatibility, and prefer the modern \
replacement API over a compatibility shim unless the package must still \
support the old interpreter.

The harness runs the full test suite between your turns and reports any test \
that passed on the old interpreter and no longer passes. You do not need to \
run the suite yourself, and you do not need to add a verification step -- \
that is what the harness is for.

When you have made your changes for this turn, stop and say briefly what you \
changed and why. Keep it to a few sentences; the harness will tell you \
whether it worked.
"""


class SpendExceeded(RuntimeError):
    pass


class Agent:
    """One conversation, spanning all iterations for a single repo."""

    def __init__(
        self,
        client_factory: Callable[[], anthropic.Anthropic],
        config: RunConfig,
        ledger: Ledger,
        sandbox: Sandbox,
    ):
        # Resolved on first use, so a run that never reaches the model (e.g.
        # --max-iterations 1, which only establishes a baseline) needs no
        # credentials.
        self._client_factory = client_factory
        self._client: anthropic.Anthropic | None = None
        self.config = config
        self.ledger = ledger
        self.dispatch = build_dispatch(sandbox)
        self.messages: list[dict[str, Any]] = []
        self._fallbacks_supported = True

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = self._client_factory()
        return self._client

    def _create(self) -> Any:
        """One API call. Streams so a large max_tokens cannot hit an HTTP timeout."""
        if self.ledger.cost_usd >= self.config.max_spend_usd:
            raise SpendExceeded(
                f"spend cap reached: ${self.ledger.cost_usd:.4f} "
                f">= ${self.config.max_spend_usd:.2f}"
            )

        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": 32000,
            "system": [
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    # Stable prefix: system + tools render ahead of messages,
                    # so one breakpoint here caches both.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "tools": TOOL_DEFS,
            "messages": self.messages,
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": self.config.effort},
            # Rolling breakpoint on the last cacheable block, so each turn
            # reuses the whole conversation prefix.
            "cache_control": {"type": "ephemeral"},
        }

        if self._fallbacks_supported:
            try:
                with self.client.beta.messages.stream(
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                    **kwargs,
                ) as stream:
                    return stream.get_final_message()
            except (TypeError, anthropic.BadRequestError) as exc:
                if "fallback" not in str(exc).lower():
                    raise
                # SDK or account predates server-side fallbacks; carry on
                # without them rather than failing the run.
                self._fallbacks_supported = False
                self.ledger.event("fallbacks_disabled", reason=str(exc)[:200])

        with self.client.messages.stream(**kwargs) as stream:
            return stream.get_final_message()

    def turn(self, user_message: str) -> str:
        """Send a message and run tools until the model stops asking for them.

        Returns the final assistant text.
        """
        self.messages.append({"role": "user", "content": user_message})

        while True:
            response = self._create()
            self.ledger.record_usage(response.usage)

            if response.stop_reason == "refusal":
                category = getattr(response.stop_details, "category", None)
                self.ledger.event("refusal", category=category)
                raise RuntimeError(f"model declined the request (category={category})")

            for block in response.content:
                if block.type == "thinking" and block.thinking:
                    self.ledger.event("thinking", text=block.thinking[:2000])

            self.messages.append({"role": "assistant", "content": response.content})

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                text = "\n".join(b.text for b in response.content if b.type == "text")
                self.ledger.event("assistant_text", text=text[:2000])
                return text

            results = []
            for block in tool_uses:
                self.ledger.tool_calls += 1
                output, is_error = self.dispatch(block.name, dict(block.input))
                self.ledger.event(
                    "tool_call",
                    tool=block.name,
                    args=tool_arg_preview(dict(block.input)),
                    is_error=is_error,
                    output_chars=len(output),
                )
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output or "(empty)",
                        "is_error": is_error,
                    }
                )

            # All results for one assistant turn go back in a single user
            # message -- splitting them trains the model out of parallel calls.
            self.messages.append({"role": "user", "content": results})
