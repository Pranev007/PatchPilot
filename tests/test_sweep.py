"""Tests for sweep aggregation.

The sweep table is a claim about which effort level is worth paying for, so
the arithmetic behind it needs to be right -- particularly the denominator.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from patchpilot.config import RunConfig  # noqa: E402
from patchpilot.sweep import (  # noqa: E402
    Cell,
    load_cells,
    render_sweep,
    write_csv,
)


def result(repo: str, outcome: str, cost: float, iters: int = 1) -> dict:
    return {
        "repo": repo,
        "model": "claude-opus-5",
        "outcome": outcome,
        "iterations": iters,
        "iterations_used": iters,
        "cost_usd": cost,
        "tool_calls": 5,
        "wall_seconds": 60.0,
        "api_calls": 3,
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }


def test_success_rate_excludes_skips_from_denominator():
    """Skips measure repo selection, not the agent.

    If they counted, a level that happened to skip more repos would look
    better or worse for reasons unrelated to reasoning effort.
    """
    cell = Cell(
        effort="high",
        repeat=1,
        results=[
            result("a", "success", 0.2),
            result("b", "failed_iterations", 0.5),
            result("c", "skip_install", 0.0),
            result("d", "skip_no_baseline", 0.0),
        ],
    )
    assert len(cell.attempted) == 2
    assert cell.success_rate == 0.5


def test_cost_per_success_counts_the_whole_bill():
    """Failed attempts still cost money and must stay in the numerator."""
    cell = Cell(
        effort="high",
        repeat=1,
        results=[
            result("a", "success", 0.20),
            result("b", "failed_iterations", 0.60),
        ],
    )
    assert cell.total_cost == 0.80
    assert cell.cost_per_success == 0.80  # one success, $0.80 spent to get it


def test_cost_per_success_is_none_when_nothing_landed():
    cell = Cell("low", 1, [result("a", "failed_iterations", 0.3)])
    assert cell.cost_per_success is None


def test_single_repeat_is_flagged_as_unreliable():
    cells = [Cell("high", 1, [result("a", "success", 0.2)])]
    out = render_sweep(cells, ["high"])
    assert "Single run per level" in out


def test_multiple_repeats_report_spread():
    cells = [
        Cell("high", 1, [result("a", "success", 0.2), result("b", "success", 0.2)]),
        Cell("high", 2, [result("a", "success", 0.2), result("b", "failed_iterations", 0.2)]),
    ]
    out = render_sweep(cells, ["high"])
    assert "±" in out
    assert "Single run per level" not in out


def test_render_orders_levels_as_requested():
    cells = [
        Cell("xhigh", 1, [result("a", "success", 0.9)]),
        Cell("medium", 1, [result("a", "success", 0.1)]),
    ]
    out = render_sweep(cells, ["medium", "high", "xhigh"])
    assert out.index("| medium ") < out.index("| xhigh ")


def test_load_cells_round_trips_a_sweep_directory(tmp_path):
    for effort in ("medium", "high"):
        for rep in (1, 2):
            d = tmp_path / f"effort={effort}" / f"rep{rep}" / "org__repo"
            d.mkdir(parents=True)
            (d / "result.json").write_text(
                json.dumps(result("org/repo", "success", 0.3)), encoding="utf-8"
            )
    cells = load_cells(tmp_path)
    assert len(cells) == 4
    assert {c.effort for c in cells} == {"medium", "high"}
    assert {c.repeat for c in cells} == {1, 2}


def test_csv_has_one_row_per_repo_run(tmp_path):
    cells = [
        Cell("high", 1, [result("a", "success", 0.2), result("b", "skip_install", 0.0)]),
        Cell("high", 2, [result("a", "success", 0.3), result("b", "skip_install", 0.0)]),
    ]
    path = tmp_path / "sweep.csv"
    write_csv(cells, path)
    rows = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 5  # header + 4 runs
    assert rows[0].startswith("effort,repeat,repo,outcome")


def test_effort_override_preserves_the_rest_of_the_config():
    """The sweep rebuilds RunConfig per level; nothing else may drift."""
    base = RunConfig(model="claude-opus-5", max_iterations=4, sandbox="docker")
    derived = RunConfig(**{**base.__dict__, "effort": "xhigh"})
    assert derived.effort == "xhigh"
    assert derived.max_iterations == 4
    assert derived.sandbox == "docker"
    assert derived.model == "claude-opus-5"
