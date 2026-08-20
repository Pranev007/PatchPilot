"""The agent conversation.

A hand-written tool-use loop rather than an SDK tool runner. Three reasons,
all of which are worth being able to explain:

  1. Tools are bound to a specific sandbox instance, so they cannot be
     module-level decorated functions.
  2. Every tool call is recorded in the ledger -- the trace file is what the
     failure taxonomy in the README is built from.
  3. The spend cap has to be enforced between turns, not after the fact.

The model itself sits behind `providers/`, so the same loop drives Claude,
Gemini, DeepSeek or anything else that speaks Chat Completions.

The outer verify loop (run tests -> feed regressions back) lives in runner.py.
That split is the project: the agent proposes, the test suite disposes.
"""

from __future__ import annotations

from .config import RunConfig
from .ledger import Ledger
from .providers import Provider
from .sandbox import Sandbox
from .tools import TOOL_DEFS, build_dispatch, tool_arg_preview

SYSTEM_PROMPT = """\
You are migrating a Python package from an old interpreter to a new one, in \
place, inside a sandboxed checkout.

Scope. Change only what the interpreter upgrade requires:

  - stdlib modules removed or deprecated between the two versions \
(distutils, imp, asynchat, asyncore, cgi, telnetlib, and similar)
  - APIs deprecated in favour of a replacement (datetime.utcnow, \
datetime.utcfromtimestamp, asyncio.get_event_loop in non-async context, \
locale.getdefaultlocale, pkg_resources, unittest method aliases such as \
assertEquals)
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

You must actually call the tools to make changes. Describing an edit is not \
the same as performing one -- nothing you write in prose reaches the \
repository.

The harness runs the full test suite between your turns and reports any test \
that passed on the old interpreter and no longer passes. You do not need to \
run the suite yourself, and you do not need to add a verification step -- \
that is what the harness is for.

When you have made your changes for this turn, stop and say briefly what you \
changed and why. Keep it to a few sentences; the harness will tell you \
whether it worked.
"""

_NOOP_NUDGE = """\
You ended your turn without calling any tools, so the repository is unchanged \
and re-running the tests would produce exactly the same failures.

Use read_file to inspect the relevant files and edit_file to apply the fix. \
If you believe no change is needed, say so explicitly and explain why."""


class SpendExceeded(RuntimeError):
    pass


class Agent:
    """One conversation, spanning all iterations for a single repo."""

    # A turn that calls no tools changes nothing, so it is retried rather than
    # counted. Capped because a model that will not act should fail the repo
    # rather than burn the budget being asked again.
    MAX_NOOP_RETRIES = 2

    def __init__(
        self,
        provider: Provider,
        config: RunConfig,
        ledger: Ledger,
        sandbox: Sandbox,
    ):
        self.provider = provider
        self.config = config
        self.ledger = ledger
        self.dispatch = build_dispatch(sandbox)
        self.provider.start(SYSTEM_PROMPT, TOOL_DEFS)

    def _check_budget(self) -> None:
        if self.ledger.cost_usd >= self.config.max_spend_usd:
            raise SpendExceeded(
                f"spend cap reached: ${self.ledger.cost_usd:.4f} "
                f">= ${self.config.max_spend_usd:.2f}"
            )

    def turn(self, user_message: str) -> str:
        """Send a message and run tools until the model stops asking for them.

        Returns the final assistant text.
        """
        self.provider.send_user(user_message)
        tools_used = 0
        noop_retries = 0

        while True:
            self._check_budget()
            turn = self.provider.complete()
            self.ledger.record_usage(turn.usage)

            if turn.stop_reason == "refusal":
                self.ledger.event("refusal")
                raise RuntimeError("model declined the request")

            if turn.thinking:
                self.ledger.event("thinking", text=turn.thinking[:2000])

            if turn.is_noop:
                if tools_used or noop_retries >= self.MAX_NOOP_RETRIES:
                    # Either it did real work and is now signing off, or it has
                    # refused to act twice and the iteration should end so the
                    # harness can report honestly.
                    self.ledger.event("assistant_text", text=turn.text[:2000])
                    return turn.text
                noop_retries += 1
                self.ledger.noop_retries += 1
                self.ledger.event(
                    "noop_turn", attempt=noop_retries, text=turn.text[:500]
                )
                self.provider.send_user(_NOOP_NUDGE)
                continue

            results = []
            for call in turn.tool_calls:
                tools_used += 1
                self.ledger.tool_calls += 1
                output, is_error = self.dispatch(call.name, call.args)
                self.ledger.event(
                    "tool_call",
                    tool=call.name,
                    args=tool_arg_preview(call.args),
                    is_error=is_error,
                    output_chars=len(output),
                )
                results.append((call.id, output, is_error))

            self.provider.send_tool_results(results)
