"""Effort sweep: run the whole benchmark at several reasoning-effort levels.

The output is the chart that makes the project worth reading -- success rate
against dollars, so you can say where the extra reasoning stops paying for
itself rather than just asserting that more is better.

Three things this does that a for-loop around `migrate run` would not:

  Repeats. The agent is nondeterministic, so a single run per cell gives a
  point estimate with no error bar and invites reading noise as signal.
  `--repeats 3` runs each (effort, repo) cell three times and reports spread.

  A budget. efforts x repeats x repos multiplies quickly. The sweep states
  its worst-case spend up front and stops cleanly when the cap is hit,
  rather than discovering the total afterwards.

  Resume. A full sweep runs for hours. Every cell writes result.json as it
  finishes, and --resume skips cells that already have one, so a crash or a
  Ctrl-C costs you one cell instead of the afternoon.
"""

from __future__ import annotations

import csv
import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import anthropic

from .config import RepoSpec, RunConfig
from .runner import SUCCESS, run_repo

_ATTEMPTED = {"success", "failed_iterations", "failed_spend"}


class BudgetExhausted(RuntimeError):
    pass


@dataclass
class Cell:
    """One (effort, repeat) pair: the benchmark run once at one setting."""

    effort: str
    repeat: int
    results: list[dict[str, Any]]

    @property
    def attempted(self) -> list[dict[str, Any]]:
        return [r for r in self.results if r["outcome"] in _ATTEMPTED]

    @property
    def successes(self) -> list[dict[str, Any]]:
        return [r for r in self.results if r["outcome"] == SUCCESS]

    @property
    def success_rate(self) -> float:
        """Fraction of *attempted* repos that reached a green suite.

        Skips are excluded from the denominator: they measure repo selection,
        not the agent, and letting them drift between cells would make the
        efforts incomparable.
        """
        return len(self.successes) / len(self.attempted) if self.attempted else 0.0

    @property
    def total_cost(self) -> float:
        return sum(r["cost_usd"] for r in self.results)

    @property
    def median_cost(self) -> float:
        costs = [r["cost_usd"] for r in self.attempted]
        return statistics.median(costs) if costs else 0.0

    @property
    def cost_per_success(self) -> float | None:
        """Total spend divided by repos actually migrated.

        Usually the number worth deciding on: a cheap setting that fails more
        often can easily cost more per repo that actually lands.
        """
        return self.total_cost / len(self.successes) if self.successes else None

    @property
    def median_iterations(self) -> float | None:
        iters = [r.get("iterations_used", r["iterations"]) for r in self.successes]
        return statistics.median(iters) if iters else None


def run_sweep(
    specs: list[RepoSpec],
    base_config: RunConfig,
    efforts: list[str],
    repeats: int,
    client_factory: Callable[[], anthropic.Anthropic],
    sweep_dir: Path,
    max_total_spend: float,
    resume: bool = False,
) -> list[Cell]:
    sweep_dir.mkdir(parents=True, exist_ok=True)
    (sweep_dir / "manifest.json").write_text(
        json.dumps(
            {
                "efforts": efforts,
                "repeats": repeats,
                "repos": [s.name for s in specs],
                "model": base_config.model,
                "max_iterations": base_config.max_iterations,
                "max_spend_usd_per_repo": base_config.max_spend_usd,
                "max_total_spend": max_total_spend,
                "sandbox": base_config.sandbox,
                "upgrade_test_tooling": base_config.upgrade_test_tooling,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    cells: list[Cell] = []
    spent = 0.0
    total_cells = len(efforts) * repeats
    cell_n = 0

    for effort in efforts:
        config = RunConfig(**{**base_config.__dict__, "effort": effort})
        for repeat in range(1, repeats + 1):
            cell_n += 1
            run_root = sweep_dir / f"effort={effort}" / f"rep{repeat}"
            run_root.mkdir(parents=True, exist_ok=True)
            print(
                f"\n=== cell {cell_n}/{total_cells}: effort={effort} rep={repeat} "
                f"(spent ${spent:.2f}/{max_total_spend:.2f}) ===",
                flush=True,
            )

            results = []
            for spec in specs:
                cached = run_root / spec.slug / "result.json"
                if resume and cached.exists():
                    result = json.loads(cached.read_text(encoding="utf-8"))
                    print(f"  {spec.name}: cached ({result['outcome']})", flush=True)
                    results.append(result)
                    spent += result["cost_usd"]
                    continue

                if spent >= max_total_spend:
                    raise BudgetExhausted(
                        f"sweep budget exhausted at ${spent:.2f}; "
                        f"{total_cells - cell_n + 1} cells not run. "
                        f"Re-run with --resume and a higher --max-total-spend."
                    )

                result = run_repo(spec, config, client_factory, run_root)
                results.append(result)
                spent += result["cost_usd"]
                print(
                    f"  {spec.name}: {result['outcome']} "
                    f"${result['cost_usd']:.3f}",
                    flush=True,
                )

            cells.append(Cell(effort=effort, repeat=repeat, results=results))

    return cells


def load_cells(sweep_dir: Path) -> list[Cell]:
    """Rebuild cells from disk, so reporting does not require re-running."""
    cells = []
    for effort_dir in sorted(sweep_dir.glob("effort=*")):
        effort = effort_dir.name.split("=", 1)[1]
        for rep_dir in sorted(effort_dir.glob("rep*")):
            results = [
                json.loads(p.read_text(encoding="utf-8"))
                for p in sorted(rep_dir.glob("*/result.json"))
            ]
            if results:
                cells.append(
                    Cell(
                        effort=effort,
                        repeat=int(rep_dir.name[3:]),
                        results=results,
                    )
                )
    return cells


def _spread(values: list[float]) -> str:
    if not values:
        return "-"
    if len(values) == 1:
        return f"{values[0] * 100:.0f}%"
    return f"{statistics.mean(values) * 100:.0f}% ± {statistics.pstdev(values) * 100:.0f}"


def render_sweep(cells: list[Cell], order: list[str]) -> str:
    if not cells:
        return "No sweep results found."

    by_effort: dict[str, list[Cell]] = {}
    for cell in cells:
        by_effort.setdefault(cell.effort, []).append(cell)

    efforts = [e for e in order if e in by_effort]
    repeats = max(len(v) for v in by_effort.values())

    lines = ["## Effort sweep\n"]
    lines.append(
        f"{len(efforts)} effort levels x {repeats} repeat(s) x "
        f"{len(cells[0].results)} repos. "
        "Success rate is over *attempted* repos; skips are excluded from the "
        "denominator so the levels stay comparable.\n"
    )
    lines.append(
        "| Effort | Success rate | Median $/repo | $/success | Median iters | Total $ |"
    )
    lines.append("|---|---|---:|---:|---:|---:|")

    for effort in efforts:
        group = by_effort[effort]
        rates = [c.success_rate for c in group]
        per_success = [c.cost_per_success for c in group if c.cost_per_success]
        iters = [c.median_iterations for c in group if c.median_iterations]
        lines.append(
            "| {e} | {rate} | ${med:.3f} | {ps} | {it} | ${tot:.2f} |".format(
                e=effort,
                rate=_spread(rates),
                med=statistics.mean([c.median_cost for c in group]),
                ps=f"${statistics.mean(per_success):.3f}" if per_success else "-",
                it=f"{statistics.mean(iters):.1f}" if iters else "-",
                tot=sum(c.total_cost for c in group),
            )
        )

    lines.append("")
    if repeats == 1:
        lines.append(
            "> Single run per level -- the spread is unknown, so small "
            "differences between levels are not yet distinguishable from "
            "noise. Re-run with `--repeats 3` before drawing conclusions.\n"
        )
    return "\n".join(lines) + "\n"


def write_csv(cells: list[Cell], path: Path) -> None:
    """Per-repo rows, so the sweep can be re-analysed without re-running it."""
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["effort", "repeat", "repo", "outcome", "iterations",
             "cost_usd", "tool_calls", "wall_seconds"]
        )
        for cell in cells:
            for r in cell.results:
                writer.writerow(
                    [
                        cell.effort,
                        cell.repeat,
                        r["repo"],
                        r["outcome"],
                        r.get("iterations_used", r["iterations"]),
                        f"{r['cost_usd']:.4f}",
                        r["tool_calls"],
                        r["wall_seconds"],
                    ]
                )


def write_plot(cells: list[Cell], order: list[str], path: Path) -> str:
    """Success rate against median cost, one point per effort level.

    Returns a status string. matplotlib is optional: the CSV and the table are
    the durable artifacts, and a missing plotting library should not fail a
    sweep that took an hour to run.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return "matplotlib not installed -- skipped plot (pip install matplotlib)"

    by_effort: dict[str, list[Cell]] = {}
    for cell in cells:
        by_effort.setdefault(cell.effort, []).append(cell)
    efforts = [e for e in order if e in by_effort]
    if not efforts:
        return "no cells to plot"

    xs = [statistics.mean([c.median_cost for c in by_effort[e]]) for e in efforts]
    ys = [statistics.mean([c.success_rate for c in by_effort[e]]) * 100 for e in efforts]
    errs = [
        statistics.pstdev([c.success_rate for c in by_effort[e]]) * 100
        if len(by_effort[e]) > 1
        else 0.0
        for e in efforts
    ]

    n_repos = len(cells[0].results)
    n_repeats = max(len(v) for v in by_effort.values())

    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=150)
    ax.errorbar(xs, ys, yerr=errs, marker="o", capsize=4, linewidth=1.5)
    for x, y, label in zip(xs, ys, efforts):
        ax.annotate(
            label, (x, y), textcoords="offset points", xytext=(7, 5), fontsize=9
        )
    ax.set_xlabel("Median cost per repo (USD)")
    ax.set_ylabel("Repos migrated to green (%)")
    # pad leaves room for the sample-size line below the title
    ax.set_title("Reasoning effort vs. migration success", pad=24)
    # Sample size belongs on the chart. Error bars this wide are the honest
    # rendering of a small sweep, and a reader who cannot see n will read the
    # line between the points as a trend it does not support.
    ax.text(
        0.5,
        1.02,
        f"{n_repos} repos x {n_repeats} repeat(s); bars are 1 SD across repeats",
        transform=ax.transAxes,
        ha="center",
        fontsize=8,
        color="0.4",
    )
    ax.grid(alpha=0.3)
    ax.set_ylim(bottom=0)
    ax.margins(x=0.12)  # keep the rightmost label inside the axes
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return f"plot written to {path}"
