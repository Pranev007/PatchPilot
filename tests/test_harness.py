"""Tests for the verification oracle.

These run without an API key, Docker, or network. The regression comparison is
the one piece of logic that, if wrong, silently corrupts every number in the
results table -- so it gets tested directly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from migrator.harness import SuiteReport, failure_digest, run_tests  # noqa: E402
from migrator.sandbox import ExecResult, LocalVenvSandbox  # noqa: E402


class FakeSandbox:
    """Writes a canned pytest JSON report instead of running pytest."""

    def __init__(self, workdir: Path, payload: dict | None):
        self.workdir = workdir
        self._payload = payload

    def exec(self, cmd, timeout):  # noqa: ARG002
        if self._payload is not None:
            (self.workdir / ".migrator-report.json").write_text(
                json.dumps(self._payload), encoding="utf-8"
            )
        return ExecResult(0, "collected 3 items", "")


def test_regressions_ignores_pre_existing_failures():
    baseline = SuiteReport(collected=3, passing={"a", "b"}, failing={"c"}, parsed=True)
    after = SuiteReport(collected=3, passing={"a", "b"}, failing={"c"}, parsed=True)
    # `c` was already failing before the migration -- not the agent's problem.
    assert after.regressions(baseline) == set()


def test_regressions_catches_a_broken_test():
    baseline = SuiteReport(collected=2, passing={"a", "b"}, parsed=True)
    after = SuiteReport(collected=2, passing={"a"}, failing={"b"}, parsed=True)
    assert after.regressions(baseline) == {"b"}


def test_newly_passing_tests_are_not_regressions():
    baseline = SuiteReport(collected=2, passing={"a"}, failing={"b"}, parsed=True)
    after = SuiteReport(collected=2, passing={"a", "b"}, parsed=True)
    assert after.regressions(baseline) == set()


def test_report_is_unusable_when_nothing_collected():
    assert not SuiteReport(collected=0, parsed=True).usable
    assert not SuiteReport(collected=5, parsed=False).usable
    assert SuiteReport(collected=5, parsed=True, passing={"a"}).usable


def test_collected_but_never_run_is_not_a_usable_baseline():
    """Regression test for a false-green.

    pytest reports a collection count even when an import error stops the run
    before any test executes (exit code 2). Gating on `collected > 0` accepts
    that as a baseline with an empty passing set -- and then every subsequent
    run has zero regressions, so a repo the harness never actually tested is
    reported as successfully migrated.
    """
    baseline = SuiteReport(collected=66, passing=set(), parsed=True, exit_code=2)
    assert not baseline.usable

    # The shape of the bug, had the gate let it through:
    after = SuiteReport(collected=0, passing=set(), parsed=True, exit_code=2)
    assert after.regressions(baseline) == set()  # "no regressions" -- meaningless


def test_run_tests_parses_outcomes(tmp_path):
    payload = {
        "summary": {"collected": 3},
        "tests": [
            {"nodeid": "t.py::test_a", "outcome": "passed"},
            {"nodeid": "t.py::test_b", "outcome": "failed"},
            {"nodeid": "t.py::test_c", "outcome": "skipped"},
        ],
    }
    report = run_tests(FakeSandbox(tmp_path, payload), "pytest", timeout=10)
    assert report.parsed
    assert report.collected == 3
    assert report.passing == {"t.py::test_a"}
    assert report.failing == {"t.py::test_b"}
    assert report.skipped == {"t.py::test_c"}
    # The report file is consumed, not left behind to pollute the next run.
    assert not (tmp_path / ".migrator-report.json").exists()


def test_run_tests_survives_a_collection_crash(tmp_path):
    """No JSON report written -> unusable, not a false green."""
    report = run_tests(FakeSandbox(tmp_path, None), "pytest", timeout=10)
    assert not report.parsed
    assert not report.usable


def test_failure_digest_leads_with_traceback_when_suite_never_ran():
    """An import-time failure should not be reported as N broken tests.

    Listing every baseline node ID buries the single traceback that explains
    the failure, and costs tokens on every iteration.
    """
    baseline = SuiteReport(collected=421, passing={f"t{i}" for i in range(421)}, parsed=True)
    after = SuiteReport(
        collected=0, parsed=True, exit_code=1, raw="AttributeError: __spec__"
    )
    digest = failure_digest(after, baseline)
    assert "does not run on the new interpreter at all" in digest
    assert "AttributeError: __spec__" in digest
    assert "  - t0" not in digest


def test_failure_digest_lists_only_regressions():
    baseline = SuiteReport(passing={"a", "b"}, parsed=True, collected=3)
    after = SuiteReport(passing={"a"}, failing={"b", "c"}, parsed=True, collected=3)
    digest = failure_digest(after, baseline)
    assert "  - b" in digest
    # `c` was failing at baseline too -- keep it out so the agent stays on task.
    assert "  - c" not in digest


def test_sandbox_rejects_path_traversal(tmp_path):
    sandbox = LocalVenvSandbox(tmp_path, "unused", "unused")
    sandbox.workdir.mkdir(parents=True)
    with pytest.raises(ValueError, match="escapes repo root"):
        sandbox._resolve("../../etc/passwd")


def test_sandbox_allows_paths_inside_the_repo(tmp_path):
    sandbox = LocalVenvSandbox(tmp_path, "unused", "unused")
    sandbox.workdir.mkdir(parents=True)
    sandbox.write("pkg/mod.py", "x = 1\n")
    assert sandbox.read("pkg/mod.py") == "x = 1\n"
    assert sandbox.list_files("**/*.py") == ["pkg/mod.py"]
