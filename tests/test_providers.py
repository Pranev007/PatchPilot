"""Tests for the provider layer and the no-op-turn guard.

No network, no API key. The guard in particular is worth testing directly:
the failure it defends against (a model that describes an edit instead of
performing one) looks identical in the results table to the model simply
being bad at migrations, so getting it wrong would corrupt the benchmark's
interpretation rather than crash it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from patchpilot.agent import Agent  # noqa: E402
from patchpilot.config import PRICES, RunConfig  # noqa: E402
from patchpilot.ledger import Ledger  # noqa: E402
from patchpilot.providers.base import ToolCall, Turn, Usage, price_for  # noqa: E402
from patchpilot.providers.openai_provider import to_openai_tools  # noqa: E402


class FakeProvider:
    """Replays a scripted list of Turns and records what it was sent."""

    name = "fake"

    def __init__(self, turns: list[Turn]):
        self._turns = list(turns)
        self.sent_user: list[str] = []
        self.sent_results: list[list[tuple[str, str, bool]]] = []
        self.started = False

    def start(self, system, tools):
        self.started = True

    def send_user(self, text):
        self.sent_user.append(text)

    def send_tool_results(self, results):
        self.sent_results.append(results)

    def complete(self):
        return self._turns.pop(0) if self._turns else Turn(text="done")


class FakeSandbox:
    workdir = Path(".")

    def read(self, path):
        return "x = 1\n"

    def write(self, path, content):
        pass

    def list_files(self, glob):
        return []

    def exec(self, cmd, timeout):
        raise AssertionError("not used")


def make_agent(turns, tmp_path):
    ledger = Ledger(repo="r", model="claude-opus-5", path=tmp_path / "t.jsonl")
    provider = FakeProvider(turns)
    agent = Agent(provider, RunConfig(), ledger, FakeSandbox())
    return agent, provider, ledger


# --- tool schema translation -------------------------------------------


def test_tool_schemas_convert_to_openai_shape():
    anthropic_style = [
        {
            "name": "read_file",
            "description": "Read a file.",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        }
    ]
    out = to_openai_tools(anthropic_style)
    assert out[0]["type"] == "function"
    fn = out[0]["function"]
    assert fn["name"] == "read_file"
    # input_schema becomes parameters; the schema itself is unchanged
    assert fn["parameters"] == anthropic_style[0]["input_schema"]


# --- pricing lookup -----------------------------------------------------


def test_price_lookup_tolerates_provider_prefixes():
    """`deepseek/deepseek-v4-flash` must not silently cost $0.00."""
    assert price_for("deepseek/deepseek-v4-flash", PRICES) is not None
    assert price_for("claude-opus-5", PRICES) is not None


def test_price_lookup_returns_none_for_unknown_models():
    assert price_for("some-model-nobody-priced", PRICES) is None


# --- the no-op guard ----------------------------------------------------


def test_noop_turn_is_retried_not_counted(tmp_path):
    """A turn with no tool calls changed nothing, so it must be re-prompted."""
    turns = [
        Turn(text="I would change setup.py."),          # no-op
        Turn(tool_calls=[ToolCall("1", "read_file", {"path": "a.py"})]),
        Turn(text="Edited it."),                        # legitimate sign-off
    ]
    agent, provider, ledger = make_agent(turns, tmp_path)
    out = agent.turn("fix it")

    assert out == "Edited it."
    assert ledger.noop_retries == 1
    # the nudge was sent, and it explains that prose does not reach the repo
    assert any("without calling any tools" in m for m in provider.sent_user)


def test_noop_after_real_work_is_a_normal_finish(tmp_path):
    """Signing off after using tools is correct behaviour, not a no-op."""
    turns = [
        Turn(tool_calls=[ToolCall("1", "read_file", {"path": "a.py"})]),
        Turn(text="Bumped the pin."),
    ]
    agent, _, ledger = make_agent(turns, tmp_path)
    assert agent.turn("fix it") == "Bumped the pin."
    assert ledger.noop_retries == 0


def test_persistent_noop_gives_up_instead_of_looping(tmp_path):
    """A model that will not act should fail the repo, not drain the budget."""
    turns = [Turn(text="I recommend editing setup.py.") for _ in range(10)]
    agent, _, ledger = make_agent(turns, tmp_path)
    agent.turn("fix it")
    assert ledger.noop_retries == Agent.MAX_NOOP_RETRIES


def test_spend_cap_stops_the_turn(tmp_path):
    from patchpilot.agent import SpendExceeded

    ledger = Ledger(repo="r", model="claude-opus-5", path=tmp_path / "t.jsonl")
    ledger.output_tokens = 10_000_000  # far past any sane cap
    agent = Agent(
        FakeProvider([Turn(text="hi")]),
        RunConfig(max_spend_usd=0.50),
        ledger,
        FakeSandbox(),
    )
    with pytest.raises(SpendExceeded):
        agent.turn("go")


def test_refusal_is_surfaced_not_swallowed(tmp_path):
    agent, _, _ = make_agent([Turn(stop_reason="refusal")], tmp_path)
    with pytest.raises(RuntimeError, match="declined"):
        agent.turn("go")


# --- usage accounting ---------------------------------------------------


def test_usage_accumulates_across_calls(tmp_path):
    turns = [
        Turn(tool_calls=[ToolCall("1", "read_file", {"path": "a.py"})],
             usage=Usage(input_tokens=100, output_tokens=20, cache_read_tokens=50)),
        Turn(text="done", usage=Usage(input_tokens=200, output_tokens=30)),
    ]
    agent, _, ledger = make_agent(turns, tmp_path)
    agent.turn("go")
    assert ledger.input_tokens == 300
    assert ledger.output_tokens == 50
    assert ledger.cache_read_tokens == 50
    assert ledger.api_calls == 2
    assert ledger.cost_usd > 0


# --- provider extension fields -----------------------------------------


class _FakeFn:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeCall:
    def __init__(self, id, name, arguments, model_extra=None):
        self.id = id
        self.function = _FakeFn(name, arguments)
        self.model_extra = model_extra or {}


def test_tool_call_echo_preserves_provider_extensions():
    """Regression test: Gemini 3.x rejects a follow-up without this.

    The signature is attached under extra_content.google and the next request
    fails with "Function call is missing a thought_signature" if the compat
    layer rebuilds the message without it -- which breaks multi-turn tool use
    completely, on the second call of every repo.
    """
    from patchpilot.providers.openai_provider import _echo_tool_call

    extra = {"google": {"thought_signature": "abc123"}}
    out = _echo_tool_call(
        _FakeCall("call_1", "list_files", '{"glob":"*.py"}',
                  {"extra_content": extra})
    )
    assert out["extra_content"] == extra
    assert out["function"]["name"] == "list_files"
    assert out["id"] == "call_1"


def test_tool_call_echo_omits_extension_when_absent():
    """Providers that send nothing extra must not gain an empty field."""
    from patchpilot.providers.openai_provider import _echo_tool_call

    out = _echo_tool_call(_FakeCall("call_1", "read_file", "{}"))
    assert "extra_content" not in out
