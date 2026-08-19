"""Aggregate run results into the markdown table that goes in the README.

The table is the deliverable. Generate it from `result.json` files rather
than transcribing numbers by hand, so it cannot drift from what actually ran.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from .runner import (
    ERROR,
    FAILED_CAP,
    FAILED_SPEND,
    SKIP_INSTALL,
    SKIP_NO_BASELINE,
    SUCCESS,
)

_ATTEMPTED = {SUCCESS, FAILED_CAP, FAILED_SPEND}
_LABELS = {
    SUCCESS: "green",
    FAILED_CAP: "hit iteration cap",
    FAILED_SPEND: "hit spend cap",
    SKIP_NO_BASELINE: "skipped (no baseline)",
    SKIP_INSTALL: "skipped (would not build)",
    ERROR: "harness error",
}


def load_results(run_dir: Path) -> list[dict[str, Any]]:
    results = []
    for path in sorted(run_dir.glob("*/result.json")):
        results.append(json.loads(path.read_text(encoding="utf-8")))
    return results


def render(results: list[dict[str, Any]]) -> str:
    if not results:
        return "No results found."

    attempted = [r for r in results if r["outcome"] in _ATTEMPTED]
    succeeded = [r for r in results if r["outcome"] == SUCCESS]
    skipped = [r for r in results if r["outcome"].startswith("skip")]

    lines: list[str] = []
    lines.append("## Results\n")

    # Headline numbers. Report the rate over *attempted* repos and state the
    # skip count next to it -- a success rate that quietly excludes the repos
    # that would not build is the classic way to inflate a benchmark.
    if attempted:
        rate = len(succeeded) / len(attempted) * 100
        costs = [r["cost_usd"] for r in attempted]
        iters = [r.get("iterations_used", r["iterations"]) for r in succeeded]
        lines.append(
            f"**{len(succeeded)}/{len(attempted)} repos migrated to a green test "
            f"suite ({rate:.0f}%)**, from {len(results)} candidates "
            f"({len(skipped)} skipped before the agent ran).\n"
        )
        lines.append(f"- Median cost per attempted repo: **${statistics.median(costs):.3f}**")
        lines.append(f"- Total spend: ${sum(costs):.2f}")
        if iters:
            lines.append(
                f"- Median iterations to green: **{statistics.median(iters):.1f}**"
            )
        lines.append("")

    lines.append("| Repo | Outcome | Iters | Baseline pass | Cost | Tool calls | Wall (s) |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for r in sorted(results, key=lambda r: (r["outcome"] != SUCCESS, r["repo"])):
        lines.append(
            "| {repo} | {label} | {iters} | {base} | ${cost:.3f} | {tools} | {wall:.0f} |".format(
                repo=r["repo"],
                label=_LABELS.get(r["outcome"], r["outcome"]),
                iters=r.get("iterations_used", r["iterations"]) or "-",
                base=r.get("baseline_passing", "-"),
                cost=r["cost_usd"],
                tools=r["tool_calls"],
                wall=r["wall_seconds"],
            )
        )

    lines.append("")
    lines.append("### Outcome breakdown\n")
    counts: dict[str, int] = {}
    for r in results:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    for outcome, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {_LABELS.get(outcome, outcome)}: {n}")

    return "\n".join(lines) + "\n"
