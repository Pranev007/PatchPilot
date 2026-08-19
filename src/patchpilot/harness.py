"""The verification oracle.

This module is the methodological core of the benchmark. Everything else is
plumbing around one idea:

    A repo counts as migrated only if every test that passed on the OLD
    interpreter still passes on the NEW one.

That framing is what makes the number honest. Repos ship with tests that were
already broken, tests that are flaky, and tests that are skipped on some
platforms. Comparing "all tests pass" before and after would penalise the
agent for pre-existing failures it never touched. Comparing the *set of
passing node IDs* does not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .sandbox import ExecResult, Sandbox

_REPORT_FILE = ".patchpilot-report.json"

# Installed into every sandbox venv so we get per-test node IDs rather than
# having to scrape pytest's human-readable summary.
_HARNESS_DEPS = ["pytest", "pytest-json-report"]


@dataclass
class SuiteReport:
    collected: int = 0
    passing: set[str] = field(default_factory=set)
    failing: set[str] = field(default_factory=set)
    skipped: set[str] = field(default_factory=set)
    raw: str = ""
    parsed: bool = False
    exit_code: int = -1

    @property
    def usable(self) -> bool:
        """Is this baseline something we can actually verify a migration against?

        The bar is "at least one test passed", NOT "some tests were collected".
        pytest reports a collection count even when an import error stops the
        run before a single test executes, and a baseline with zero passing
        tests makes every later run trivially regression-free -- the harness
        would hand back a confident green for a repo it never tested.

        Missing test-only dependencies are the usual cause. Take the install
        recipe from the repo CI config (tox.ini, the GitHub Actions workflow)
        rather than assuming `pip install -e .` is enough.
        """
        return self.parsed and bool(self.passing)

    def regressions(self, baseline: SuiteReport) -> set[str]:
        """Tests that passed at baseline and no longer pass."""
        return baseline.passing - self.passing

    def summary(self) -> str:
        return (
            f"exit={self.exit_code} collected={self.collected} "
            f"passed={len(self.passing)} failed={len(self.failing)} "
            f"skipped={len(self.skipped)}"
        )


def install(
    sandbox: Sandbox,
    spec_install: list[str],
    timeout: int,
    upgrade_test_tooling: bool = True,
) -> ExecResult:
    """Install the repo and the harness deps into the sandbox environment.

    `spec_install` comes from the repo's own CI config. If it fails, the repo
    is unbuildable in this environment and gets excluded from the benchmark --
    that exclusion is a finding, not a bug, and belongs in the results table.

    `upgrade_test_tooling` decides what the benchmark is actually measuring,
    and it is a real methodological choice rather than a convenience flag:

      True (default) -- force pytest to a current release, overriding any pin
        in the repo test requirements. Measures migration of the PACKAGE, with
        the test runner held constant. Without this, repos that pin an old
        pytest fail before a single test runs (old pytest imports `py.path`,
        which breaks on 3.12), and the agent spends its first iterations
        unpinning pytest in every repo -- uniform, boring, and not the thing
        under study.

      False -- honour the repo pins. Measures migration of the package AND its
        test tooling, which is what a real upgrade actually involves. More
        faithful, more expensive, noisier.

    Report which one you used next to the results table; the numbers are not
    comparable across settings.
    """
    for raw_cmd in spec_install:
        res = sandbox.exec(raw_cmd.split(), timeout=timeout)
        if not res.ok:
            return res
    cmd = ["uv", "pip", "install"]
    if upgrade_test_tooling:
        cmd.append("--upgrade")
    return sandbox.exec([*cmd, *_HARNESS_DEPS], timeout=timeout)


def run_tests(sandbox: Sandbox, test_cmd: str, timeout: int) -> SuiteReport:
    """Run the suite and parse per-test outcomes."""
    cmd = [
        *test_cmd.split(),
        "--json-report",
        f"--json-report-file={_REPORT_FILE}",
        "-q",
        "--tb=short",
        "-p",
        "no:cacheprovider",
    ]
    res = sandbox.exec(cmd, timeout=timeout)
    report = SuiteReport(raw=res.tail(), exit_code=res.returncode)

    report_path = Path(sandbox.workdir) / _REPORT_FILE
    if not report_path.exists():
        # pytest never got far enough to write a report -- collection error,
        # missing dependency, or a crash. `raw` holds the reason.
        return report

    try:
        data = json.loads(report_path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return report
    finally:
        report_path.unlink(missing_ok=True)

    report.parsed = True
    report.collected = data.get("summary", {}).get("collected", 0)
    for test in data.get("tests", []):
        node_id = test.get("nodeid", "")
        outcome = test.get("outcome", "")
        if outcome == "passed":
            report.passing.add(node_id)
        elif outcome in ("failed", "error"):
            report.failing.add(node_id)
        elif outcome == "skipped":
            report.skipped.add(node_id)
    return report


def failure_digest(report: SuiteReport, baseline: SuiteReport, limit: int = 20) -> str:
    """The message handed back to the agent after a failed verification.

    Deliberately scoped to regressions. Feeding the agent every failure --
    including ones that were already failing before it touched anything --
    sends it chasing bugs that are not its job to fix.
    """
    regressions = sorted(report.regressions(baseline))
    if not regressions:
        return "No regressions against baseline."

    if not report.parsed or not report.passing:
        # The suite did not run at all -- usually an import-time failure, and
        # very often a pinned dependency that is itself incompatible with the
        # new interpreter. Naming all N baseline tests here would bury the one
        # traceback that actually explains it.
        return (
            f"The test suite does not run on the new interpreter at all "
            f"(exit={report.exit_code}, 0 of {len(baseline.passing)} baseline "
            f"tests executed). Fix this before anything else.\n\n"
            f"Output:\n{report.raw}"
        )

    lines = [
        f"{len(regressions)} test(s) passed on the old interpreter but do not pass now:",
        "",
    ]
    lines.extend(f"  - {node}" for node in regressions[:limit])
    if len(regressions) > limit:
        lines.append(f"  ... and {len(regressions) - limit} more")
    lines.append("")
    lines.append("Test output (tail):")
    lines.append(report.raw)
    return "\n".join(lines)
