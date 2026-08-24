"""Execution sandbox.

One backend: `LocalVenvSandbox`, a uv-managed virtualenv per repository on the
host. Fast, and **not isolated** -- `pip install -e .` executes the target
repository's `setup.py`, and its tests run as you. Only point it at
repositories you have read.

A container backend existed here and was deleted. It was written, wired into
the CLI, recommended by the README, and had never once been executed, because
the machine running the benchmark had no Docker daemon and no test could cover
it. Ninety-eight lines of plausible, unverified code presented as the safe
option is worse than not offering the option: it invites trust the code has
not earned. Every published result was produced by the backend below, so that
is the only one the repository ships.

The `Sandbox` protocol is kept. It costs nothing, it documents what a second
backend would have to provide, and adding one is a contained change -- but the
next backend gets merged with a run behind it.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class ExecResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def tail(self, limit: int = 6000) -> str:
        """Combined output, truncated from the front.

        Test failures live at the end of pytest output, so keep the tail.
        """
        combined = (self.stdout + "\n" + self.stderr).strip()
        if len(combined) <= limit:
            return combined
        return "...[truncated]...\n" + combined[-limit:]


class Sandbox(Protocol):
    workdir: Path
    python_path: str

    def setup(self, python_version: str) -> None: ...
    def provision_env(self, python_version: str) -> None: ...
    def exec(self, cmd: list[str], timeout: int) -> ExecResult: ...
    def read(self, rel_path: str) -> str: ...
    def write(self, rel_path: str, content: str) -> None: ...
    def list_files(self, glob: str) -> list[str]: ...
    def teardown(self) -> None: ...


def pin_interpreter(cmd: list[str], python_path: str) -> list[str]:
    """Rewrite a command so it cannot possibly hit the host interpreter.

    Repo specs are copied verbatim out of CI configs, so they say things like
    `python -m pytest` and `uv pip install -e .` with no notion of which
    environment they land in. Relying on PATH and VIRTUAL_ENV to steer them is
    not enough: uv in particular walks up the directory tree looking for a
    project to install into, and will happily pick a venv above the sandbox.

    Getting this wrong is not a crash -- it is a benchmark that runs against
    the wrong Python and reports numbers that look completely plausible.
    """
    if not cmd:
        return cmd
    if cmd[0] == "python":
        return [python_path, *cmd[1:]]
    if cmd[0] == "pytest":
        return [python_path, "-m", "pytest", *cmd[1:]]
    if cmd[0] == "uv" and len(cmd) >= 3 and cmd[1] == "pip":
        return [*cmd[:3], "--python", python_path, *cmd[3:]]
    return cmd


def verify_interpreter(sandbox: "Sandbox", expected: str) -> None:
    """Assert the sandbox really is running the interpreter we asked for.

    Called after every setup. The failure mode this guards against is silent:
    if the sandbox falls through to the host Python, every repo still builds,
    every test still runs, and the benchmark reports a number that measures
    nothing. Better to stop the run than to publish that.
    """
    res = sandbox.exec(
        ["python", "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
        timeout=120,
    )
    actual = res.stdout.strip()
    if not res.ok or actual != expected:
        raise RuntimeError(
            f"sandbox interpreter mismatch: asked for {expected}, got "
            f"{actual or '<no output>'} (exit {res.returncode}). {res.tail(500)}"
        )


def _run(
    cmd: list[str], cwd: Path | None, timeout: int, env: dict | None = None
) -> ExecResult:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            errors="replace",
        )
        return ExecResult(proc.returncode, proc.stdout, proc.stderr)
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", "replace")
        return ExecResult(-1, partial, f"TIMEOUT after {timeout}s", timed_out=True)
    except FileNotFoundError as exc:
        return ExecResult(-1, "", f"command not found: {exc}")


def rmtree(path: Path) -> None:
    """Delete a tree, including git's read-only object files.

    On Windows `shutil.rmtree` fails on the read-only files under `.git`, and
    with `ignore_errors=True` it fails *silently* -- leaving a half-deleted
    directory that makes the next clone error out.
    """

    def _chmod_retry(func, target, _exc):
        import stat

        Path(target).chmod(stat.S_IWRITE)
        func(target)

    if path.exists():
        shutil.rmtree(path, onerror=_chmod_retry)


def clone(url: str, ref: str, dest: Path, timeout: int = 300) -> ExecResult:
    dest.parent.mkdir(parents=True, exist_ok=True)
    rmtree(dest)
    res = _run(["git", "clone", "--quiet", url, str(dest)], cwd=None, timeout=timeout)
    if not res.ok:
        return res
    return _run(["git", "checkout", "--quiet", ref], cwd=dest, timeout=60)


class LocalVenvSandbox:
    """Host-local sandbox using `uv venv`. Requires uv on PATH."""

    def __init__(self, root: Path, repo_url: str, repo_ref: str):
        # Absolute throughout. A relative root combined with `cwd=` on the
        # subprocess silently creates the venv one level deeper than intended,
        # and everything downstream then falls back to the host interpreter.
        self.root = root.resolve()
        self.workdir = self.root / "repo"
        self.venv = self.root / ".venv"
        self._repo_url = repo_url
        self._repo_ref = repo_ref

    @property
    def _bin(self) -> Path:
        return self.venv / ("Scripts" if platform.system() == "Windows" else "bin")

    @property
    def python(self) -> Path:
        exe = "python.exe" if platform.system() == "Windows" else "python"
        return self._bin / exe

    @property
    def python_path(self) -> str:
        return str(self.python)

    def setup(self, python_version: str) -> None:
        res = clone(self._repo_url, self._repo_ref, self.workdir)
        if not res.ok:
            raise RuntimeError(f"clone failed: {res.tail()}")
        self.provision_env(python_version)

    def provision_env(self, python_version: str) -> None:
        """Replace the environment, leaving the checkout untouched.

        This is how the interpreter switch happens: same working tree, new
        venv, so anything that breaks is attributable to the upgrade and not
        to a fresh clone.
        """
        rmtree(self.venv)
        # uv downloads the interpreter if it is not already present.
        res = _run(
            ["uv", "venv", "--python", python_version, str(self.venv)],
            cwd=self.root,
            timeout=600,
        )
        if not res.ok:
            raise RuntimeError(f"uv venv --python {python_version} failed: {res.tail()}")
        verify_interpreter(self, python_version)

    def exec(self, cmd: list[str], timeout: int) -> ExecResult:
        """Run a command with the sandbox venv first on PATH."""
        env = os.environ.copy()
        env["PATH"] = str(self._bin) + os.pathsep + env.get("PATH", "")
        env["VIRTUAL_ENV"] = str(self.venv)
        env["UV_PROJECT_ENVIRONMENT"] = str(self.venv)
        env.pop("PYTHONHOME", None)
        return _run(
            pin_interpreter(cmd, self.python_path),
            cwd=self.workdir,
            timeout=timeout,
            env=env,
        )

    def _resolve(self, rel_path: str) -> Path:
        """Resolve a model-supplied path, refusing anything outside the repo."""
        root = self.workdir.resolve()
        target = (root / rel_path).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"path escapes repo root: {rel_path}")
        return target

    def read(self, rel_path: str) -> str:
        return self._resolve(rel_path).read_text(encoding="utf-8", errors="replace")

    def write(self, rel_path: str, content: str) -> None:
        target = self._resolve(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def list_files(self, glob: str) -> list[str]:
        root = self.workdir.resolve()
        return sorted(
            str(p.relative_to(root)).replace("\\", "/")
            for p in root.glob(glob)
            if p.is_file() and ".git" not in p.parts
        )

    def teardown(self) -> None:
        rmtree(self.venv)


def make_sandbox(kind: str, root: Path, repo_url: str, repo_ref: str) -> Sandbox:
    if kind == "local":
        return LocalVenvSandbox(root, repo_url, repo_ref)
    raise ValueError(f"unknown sandbox: {kind!r}; only 'local' is implemented")
