"""Per-run accounting: tokens, dollars, iterations, tool calls.

Every run writes one JSONL file. The benchmark table in the README is
generated from these, so anything you want in the results table has to be
recorded here.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import PRICES


@dataclass
class Ledger:
    repo: str
    model: str
    path: Path
    started_at: float = field(default_factory=time.time)

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    api_calls: int = 0
    tool_calls: int = 0
    iterations: int = 0

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    def record_usage(self, usage: Any) -> None:
        self.api_calls += 1
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0

    @property
    def cost_usd(self) -> float:
        price = PRICES.get(self.model)
        if price is None:
            return 0.0
        per_m = 1_000_000
        return (
            self.input_tokens / per_m * price["input"]
            + self.output_tokens / per_m * price["output"]
            + self.cache_write_tokens / per_m * price["input"] * 1.25
            + self.cache_read_tokens / per_m * price["input"] * 0.10
        )

    def event(self, kind: str, **payload: Any) -> None:
        """Append one trace event. This file is the failure-taxonomy source."""
        record = {
            "t": round(time.time() - self.started_at, 2),
            "repo": self.repo,
            "kind": kind,
            **payload,
        }
        self._fh.write(json.dumps(record, default=str) + "\n")
        self._fh.flush()

    def summary(self, **extra: Any) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "model": self.model,
            "iterations": self.iterations,
            "api_calls": self.api_calls,
            "tool_calls": self.tool_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cost_usd": round(self.cost_usd, 4),
            "wall_seconds": round(time.time() - self.started_at, 1),
            **extra,
        }

    def close(self) -> None:
        self._fh.close()
