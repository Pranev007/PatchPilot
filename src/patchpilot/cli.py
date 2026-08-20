"""Command line entry point.

    patchpilot run           --repos configs/repos.yaml [--sandbox docker]
    patchpilot sweep         --efforts medium high xhigh --repeats 3
    patchpilot report        --run runs/2026-08-17T12-00-00
    patchpilot sweep-report  --sweep runs/sweep-2026-08-17T12-00-00
    patchpilot doctor
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sys
from pathlib import Path

from .config import RunConfig, load_repos
from .report import load_results, render
from .runner import SUCCESS, run_repo
from .providers import KNOWN_KEY_ENVS, PROVIDERS, make_provider
from .sandbox import DockerSandbox
from .sweep import (
    BudgetExhausted,
    load_cells,
    render_sweep,
    run_sweep,
    write_csv,
    write_plot,
)


def _timestamp() -> str:
    return dt.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")


def cmd_doctor(_: argparse.Namespace) -> int:
    """Check the environment before burning API credit on a broken setup."""
    checks: list[tuple[str, bool, str]] = [
        ("git", shutil.which("git") is not None, "required, for cloning repos"),
        ("uv", shutil.which("uv") is not None, "required for --sandbox local"),
        (
            "docker",
            DockerSandbox.available(),
            "required for --sandbox docker (recommended for the full benchmark)",
        ),
    ]
    keyed = [e for e in {"ANTHROPIC_API_KEY", *KNOWN_KEY_ENVS.values()}
             if os.environ.get(e)]
    checks.append((
        "provider key",
        bool(keyed),
        f"found: {', '.join(sorted(keyed))}" if keyed
        else "set one of ANTHROPIC_API_KEY, "
             + ", ".join(sorted(set(KNOWN_KEY_ENVS.values()))),
    ))
    ok = True
    for name, passed, note in checks:
        mark = "ok  " if passed else "MISS"
        print(f"[{mark}] {name:<20} {note}")
        if not passed and name != "docker":
            ok = False
    if not ok:
        print("\nFix the MISS lines above before running.")
    return 0 if ok else 1


def _preflight_provider(args) -> int:
    """Fail fast on a bad provider config, before cloning anything.

    Skipped when --max-iterations is 1, because that path establishes a
    baseline without ever calling the model and must keep working with no
    credentials at all.
    """
    if args.max_iterations <= 1:
        return 0
    try:
        make_provider(
            provider=args.provider, model=args.model,
            effort=args.effort, base_url=args.base_url,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        print(
            "Or validate the repo set for free, which never calls the model:\n"
            "    patchpilot run --max-iterations 1",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    specs = load_repos(Path(args.repos))
    if args.only:
        wanted = set(args.only)
        specs = [s for s in specs if s.name in wanted]
        if not specs:
            print(f"No repo matched {sorted(wanted)}", file=sys.stderr)
            return 1

    if _preflight_provider(args):
        return 1

    config = RunConfig(
        provider=args.provider,
        base_url=args.base_url,
        rpm=args.rpm,
        model=args.model,
        effort=args.effort,
        max_iterations=args.max_iterations,
        max_spend_usd=args.max_spend,
        sandbox=args.sandbox,
        test_timeout=args.test_timeout,
        upgrade_test_tooling=not args.honour_test_pins,
    )

    if config.sandbox == "local":
        print(
            "WARNING: --sandbox local runs repo build scripts and tests on this "
            "machine with no isolation.\n"
            "         Fine for repos you have read; use --sandbox docker for the "
            "full benchmark.\n"
        )

    run_dir = Path(args.runs_dir) / _timestamp()
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"Run directory: {run_dir}\n")

    # Called only when a repo actually reaches the model, so
    # --max-iterations 1 (baseline only) still runs without credentials.
    def provider_factory(cfg: RunConfig):
        return make_provider(
            provider=cfg.provider, model=cfg.model,
            effort=cfg.effort, base_url=cfg.base_url, rpm=cfg.rpm,
        )

    results = []
    for i, spec in enumerate(specs, 1):
        print(f"[{i}/{len(specs)}] {spec.name} ...", flush=True)
        result = run_repo(spec, config, provider_factory, run_dir)
        results.append(result)
        marker = "PASS" if result["outcome"] == SUCCESS else result["outcome"]
        print(
            f"         {marker}  "
            f"iters={result.get('iterations_used', result['iterations'])} "
            f"cost=${result['cost_usd']:.3f} "
            f"tools={result['tool_calls']}\n",
            flush=True,
        )

    report = render(results)
    (run_dir / "RESULTS.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"Written to {run_dir / 'RESULTS.md'}")
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    specs = load_repos(Path(args.repos))
    if args.only:
        wanted = set(args.only)
        specs = [s for s in specs if s.name in wanted]
        if not specs:
            print(f"No repo matched {sorted(wanted)}", file=sys.stderr)
            return 1

    base_config = RunConfig(
        provider=args.provider,
        base_url=args.base_url,
        rpm=args.rpm,
        model=args.model,
        max_iterations=args.max_iterations,
        max_spend_usd=args.max_spend,
        sandbox=args.sandbox,
        test_timeout=args.test_timeout,
        upgrade_test_tooling=not args.honour_test_pins,
    )

    if _preflight_provider(args):
        return 1

    cells_total = len(args.efforts) * args.repeats
    worst_case = cells_total * len(specs) * args.max_spend
    sweep_dir = Path(args.sweep_dir or Path(args.runs_dir) / f"sweep-{_timestamp()}")

    print(
        f"Sweep: {len(args.efforts)} efforts x {args.repeats} repeats x "
        f"{len(specs)} repos = {cells_total * len(specs)} runs\n"
        f"Worst case spend: ${worst_case:.2f} "
        f"(per-repo cap ${args.max_spend:.2f})\n"
        f"Sweep budget:     ${args.max_total_spend:.2f} -- stops cleanly when hit\n"
        f"Directory:        {sweep_dir}\n"
    )
    if worst_case > args.max_total_spend:
        print(
            "NOTE: worst case exceeds the sweep budget, so the sweep may stop "
            "early. That is fine -- re-run the same command with --resume to "
            "continue from where it stopped.\n"
        )

    stopped_early = False
    try:
        cells = run_sweep(
            specs=specs,
            base_config=base_config,
            efforts=args.efforts,
            repeats=args.repeats,
            # Derived from the per-cell config, NOT from args: the sweep
            # varies effort per cell, and closing over args.effort here would
            # silently run every level at the same effort.
            provider_factory=lambda cfg: make_provider(
                provider=cfg.provider, model=cfg.model,
                effort=cfg.effort, base_url=cfg.base_url, rpm=cfg.rpm,
            ),
            sweep_dir=sweep_dir,
            max_total_spend=args.max_total_spend,
            resume=args.resume,
        )
    except BudgetExhausted as exc:
        print(f"\n{exc}\n")
        stopped_early = True
        cells = load_cells(sweep_dir)
    except KeyboardInterrupt:
        print("\nInterrupted. Completed cells are on disk; re-run with --resume.\n")
        stopped_early = True
        cells = load_cells(sweep_dir)

    return _emit_sweep(cells, args.efforts, sweep_dir, partial=stopped_early)


def _emit_sweep(
    cells: list, efforts: list[str], sweep_dir: Path, partial: bool
) -> int:
    if not cells:
        print("No completed cells to report.", file=sys.stderr)
        return 1

    report = render_sweep(cells, efforts)
    if partial:
        report = report.replace(
            "## Effort sweep\n",
            "## Effort sweep\n\n> **Partial sweep** -- some cells did not run. "
            "Levels with fewer repeats are less reliable.\n",
        )
    (sweep_dir / "SWEEP.md").write_text(report, encoding="utf-8")
    write_csv(cells, sweep_dir / "sweep.csv")
    plot_status = write_plot(cells, efforts, sweep_dir / "sweep.png")

    print(report)
    print(f"Written to {sweep_dir / 'SWEEP.md'}")
    print(f"          {sweep_dir / 'sweep.csv'}")
    print(f"          {plot_status}")
    return 0


def cmd_sweep_report(args: argparse.Namespace) -> int:
    """Regenerate the sweep table, CSV and plot from an existing sweep dir."""
    sweep_dir = Path(args.sweep)
    if not sweep_dir.exists():
        print(f"No such sweep directory: {sweep_dir}", file=sys.stderr)
        return 1
    cells = load_cells(sweep_dir)
    return _emit_sweep(cells, args.efforts, sweep_dir, partial=False)


def cmd_report(args: argparse.Namespace) -> int:
    run_dir = Path(args.run)
    if not run_dir.exists():
        print(f"No such run directory: {run_dir}", file=sys.stderr)
        return 1
    report = render(load_results(run_dir))
    (run_dir / "RESULTS.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


def main(argv: list[str] | None = None) -> int:
    # Reports contain non-ASCII (the +/- in the spread column, and whatever
    # arrives in repo tracebacks). The Windows console defaults to cp1252 and
    # mangles or raises on those; the files are UTF-8 either way.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="migrate", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run the migration benchmark")
    p_run.add_argument("--repos", default="configs/repos.yaml")
    p_run.add_argument("--only", nargs="*", help="restrict to these repo names")
    p_run.add_argument(
        "--provider", default="anthropic", choices=PROVIDERS,
        help="model provider; keys are read from the matching env var",
    )
    p_run.add_argument(
        "--base-url", help="override the endpoint (any OpenAI-compatible API)",
    )
    p_run.add_argument(
        "--rpm", type=float,
        help="requests/minute ceiling; defaults per provider (Gemini free is 5). "
             "0 disables spacing",
    )
    p_run.add_argument("--model", default="claude-opus-5")
    p_run.add_argument(
        "--effort",
        default="high",
        choices=["low", "medium", "high", "xhigh", "max"],
        help="xhigh is the documented default for agentic coding; sweep this",
    )
    p_run.add_argument("--max-iterations", type=int, default=6)
    p_run.add_argument(
        "--max-spend",
        type=float,
        default=float(os.environ.get("PATCHPILOT_MAX_SPEND_USD", "1.00")),
        help="hard per-repo spend cap in USD",
    )
    p_run.add_argument("--sandbox", default="local", choices=["local", "docker"])
    p_run.add_argument("--test-timeout", type=int, default=900)
    p_run.add_argument(
        "--honour-test-pins",
        action="store_true",
        help="do not upgrade pytest past the repo pin; measures the package "
        "AND its test tooling (see harness.install)",
    )
    p_run.add_argument("--runs-dir", default="runs")
    p_run.set_defaults(func=cmd_run)

    p_sweep = sub.add_parser(
        "sweep", help="run the benchmark across several effort levels"
    )
    p_sweep.add_argument("--repos", default="configs/repos.yaml")
    p_sweep.add_argument("--only", nargs="*", help="restrict to these repo names")
    p_sweep.add_argument(
        "--provider", default="anthropic", choices=PROVIDERS,
        help="model provider; keys are read from the matching env var",
    )
    p_sweep.add_argument(
        "--base-url", help="override the endpoint (any OpenAI-compatible API)",
    )
    p_sweep.add_argument("--rpm", type=float)
    p_sweep.add_argument("--model", default="claude-opus-5")
    p_sweep.add_argument(
        "--efforts",
        nargs="+",
        default=["medium", "high", "xhigh"],
        choices=["low", "medium", "high", "xhigh", "max"],
        help="effort levels to compare, cheapest first",
    )
    p_sweep.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="runs per level; >1 gives an error bar on the success rate",
    )
    p_sweep.add_argument("--effort", default="high")
    p_sweep.add_argument("--max-iterations", type=int, default=6)
    p_sweep.add_argument(
        "--max-spend", type=float, default=1.00, help="per-repo cap in USD"
    )
    p_sweep.add_argument(
        "--max-total-spend",
        type=float,
        default=20.00,
        help="cap for the whole sweep; it stops cleanly when hit",
    )
    p_sweep.add_argument(
        "--resume",
        action="store_true",
        help="skip cells that already have a result.json (use with --sweep-dir)",
    )
    p_sweep.add_argument("--sweep-dir", help="reuse an existing sweep directory")
    p_sweep.add_argument("--sandbox", default="local", choices=["local", "docker"])
    p_sweep.add_argument("--test-timeout", type=int, default=900)
    p_sweep.add_argument("--honour-test-pins", action="store_true")
    p_sweep.add_argument("--runs-dir", default="runs")
    p_sweep.set_defaults(func=cmd_sweep)

    p_sweep_report = sub.add_parser(
        "sweep-report", help="regenerate SWEEP.md, sweep.csv and sweep.png"
    )
    p_sweep_report.add_argument("--sweep", required=True)
    p_sweep_report.add_argument(
        "--efforts", nargs="+", default=["low", "medium", "high", "xhigh", "max"]
    )
    p_sweep_report.set_defaults(func=cmd_sweep_report)

    p_report = sub.add_parser("report", help="regenerate RESULTS.md for a run")
    p_report.add_argument("--run", required=True)
    p_report.set_defaults(func=cmd_report)

    p_doctor = sub.add_parser("doctor", help="check the local environment")
    p_doctor.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
