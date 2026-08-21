"""Per-repo orchestration: the verify loop.

    baseline on old interpreter
        -> switch to new interpreter
        -> loop { install, test, hand regressions to the agent }
        -> green, or give up at the cap

Outcomes are deliberately distinct. "Could not establish a baseline" is not
the same result as "the agent tried and failed", and collapsing them into one
number is the easiest way to publish a benchmark that overstates itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .agent import Agent, SpendExceeded
from .config import RepoSpec, RunConfig
from .harness import failure_digest, install, run_tests
from .ledger import Ledger
from .providers import Provider
from .sandbox import make_sandbox

# Terminal outcomes.
SUCCESS = "success"               # every baseline-passing test still passes
FAILED_CAP = "failed_iterations"  # ran out of iterations with regressions left
FAILED_SPEND = "failed_spend"     # hit the dollar cap
SKIP_NO_BASELINE = "skip_no_baseline"  # suite would not collect on the old version
SKIP_INSTALL = "skip_install"     # repo would not build on the old version
ERROR = "error"                   # harness broke, not the agent

_FIRST_TURN = """\
This repository is being migrated from Python {frm} to Python {to}.

Baseline on Python {frm}: {baseline}

{problem}

Make the changes needed for the package to work on Python {to}."""


def run_repo(
    spec: RepoSpec,
    config: RunConfig,
    provider_factory: Callable[[RunConfig], Provider],
    run_root: Path,
) -> dict[str, Any]:
    """Migrate one repo. Always returns a result dict; never raises."""
    root = run_root / spec.slug
    root.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(
        repo=spec.name,
        model=config.model,
        path=root / "trace.jsonl",
    )
    sandbox = make_sandbox(config.sandbox, root, spec.url, spec.ref)

    def finish(outcome: str, **extra: Any) -> dict[str, Any]:
        # Recorded per repo: which sandbox produced a number is part of the
        # number. A result from an unisolated local venv is not the same
        # claim as one from a container, and a reader should not have to
        # take the README's word for which was used.
        result = ledger.summary(
            outcome=outcome, sandbox=config.sandbox,
            provider=config.provider, effort=config.effort,
            max_spend_usd=config.max_spend_usd, **extra,
        )
        ledger.event("outcome", **result)
        ledger.close()
        (root / "result.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        return result

    try:
        # --- Baseline on the old interpreter ------------------------------
        ledger.event("phase", phase="baseline_setup", python=spec.from_python)
        sandbox.setup(spec.from_python)

        res = install(
            sandbox,
            spec.install,
            timeout=config.test_timeout,
            upgrade_test_tooling=config.upgrade_test_tooling,
        )
        if not res.ok:
            ledger.event("baseline_install_failed", output=res.tail(2000))
            return finish(SKIP_INSTALL, detail="repo does not build on old interpreter")

        baseline = run_tests(sandbox, spec.test, timeout=config.test_timeout)
        ledger.event("baseline", summary=baseline.summary())
        if not baseline.usable:
            # Log the output: the usual cause is a missing test-only dependency,
            # and without the tail you cannot tell that from a broken repo.
            ledger.event("baseline_unusable", output=baseline.raw[:4000])
            return finish(
                SKIP_NO_BASELINE,
                detail=(
                    f"no passing tests on the old interpreter ({baseline.summary()}) "
                    "-- nothing to verify against; check the install recipe"
                ),
            )

        baseline_passing = len(baseline.passing)

        # --- Switch to the new interpreter --------------------------------
        # Same checkout, fresh environment: only the interpreter changes, so
        # anything that breaks is attributable to the upgrade.
        ledger.event("phase", phase="target_setup", python=spec.to_python)
        sandbox.provision_env(spec.to_python)

        # --- Verify loop --------------------------------------------------
        # One Agent for the whole repo: the model keeps its earlier reasoning
        # and edits in context, and the cached prefix grows instead of being
        # rebuilt every iteration.
        agent = Agent(provider_factory(config), config, ledger, sandbox)

        problem: str | None = None
        for iteration in range(1, config.max_iterations + 1):
            ledger.iterations = iteration
            ledger.event("phase", phase="iteration", n=iteration)

            res = install(
            sandbox,
            spec.install,
            timeout=config.test_timeout,
            upgrade_test_tooling=config.upgrade_test_tooling,
        )
            if not res.ok:
                # Packaging failures are in scope: the agent has to fix them
                # before there is anything to test.
                problem = (
                    "The package does not install on the new interpreter.\n\n"
                    f"Install output:\n{res.tail(4000)}"
                )
            else:
                report = run_tests(sandbox, spec.test, timeout=config.test_timeout)
                ledger.event(
                    "test_run",
                    n=iteration,
                    summary=report.summary(),
                    output=report.raw[:3000],
                )
                regressions = report.regressions(baseline)
                if not regressions:
                    return finish(
                        SUCCESS,
                        iterations_used=iteration,
                        baseline_passing=baseline_passing,
                        final_passing=len(report.passing),
                    )
                problem = failure_digest(report, baseline)

            if iteration == config.max_iterations:
                break

            message = (
                _FIRST_TURN.format(
                    frm=spec.from_python,
                    to=spec.to_python,
                    baseline=baseline.summary(),
                    problem=problem,
                )
                if iteration == 1
                else f"Still not green.\n\n{problem}\n\nKeep going."
            )
            reply = agent.turn(message)
            ledger.event("agent_reply", n=iteration, text=reply[:1000])

        return finish(
            FAILED_CAP,
            iterations_used=config.max_iterations,
            baseline_passing=baseline_passing,
            detail="iteration cap reached with regressions outstanding",
        )

    except SpendExceeded as exc:
        return finish(FAILED_SPEND, detail=str(exc))
    except Exception as exc:  # harness fault, not an agent failure
        ledger.event("harness_error", error=f"{type(exc).__name__}: {exc}")
        return finish(ERROR, detail=f"{type(exc).__name__}: {exc}")
    finally:
        try:
            sandbox.teardown()
        except Exception:
            pass
