"""Configuration objects loaded from configs/repos.yaml and CLI flags."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Opus 5 list prices, USD per million tokens. Cache write is 1.25x input,
# cache read is 0.1x input.
PRICES: dict[str, dict[str, float]] = {
    # Anthropic
    "claude-opus-5": {"input": 5.00, "output": 25.00},
    "claude-sonnet-5": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
    # OpenAI-compatible endpoints. Prices move; check the provider before
    # quoting a cost figure anywhere it matters. An unknown model reports
    # $0.00, which `patchpilot doctor` warns about rather than hiding.
    "deepseek-v4-flash": {"input": 0.068, "output": 0.168},
    "nemotron-3-ultra-550b-a55b": {"input": 0.0, "output": 0.0},
    "gemini-2.0-flash": {"input": 0.0, "output": 0.0},
}


@dataclass(frozen=True)
class RepoSpec:
    """One benchmark repository.

    `install` and `test` come from the repo's own CI config -- that is the
    selection filter: if a repo does not tell you how to build and test it,
    it does not go in the benchmark.
    """

    name: str
    url: str
    ref: str
    from_python: str
    to_python: str
    install: list[str]
    test: str
    notes: str = ""

    @property
    def slug(self) -> str:
        return self.name.replace("/", "__")


@dataclass(frozen=True)
class RunConfig:
    provider: str = "anthropic"
    model: str = "claude-opus-5"
    base_url: str | None = None
    effort: str = "high"
    max_iterations: int = 6
    max_spend_usd: float = 1.00
    sandbox: str = "local"  # "local" | "docker"
    # See harness.install() -- decides whether the benchmark measures the
    # package alone or the package plus its test-tooling pins.
    upgrade_test_tooling: bool = True
    test_timeout: int = 900
    runs_dir: Path = field(default=Path("runs"))


def load_repos(path: Path) -> list[RepoSpec]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    defaults = raw.get("defaults", {})
    specs = []
    for entry in raw["repos"]:
        merged = {**defaults, **entry}
        specs.append(RepoSpec(**merged))
    return specs
