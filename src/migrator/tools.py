"""Tool surface exposed to the agent.

Five tools, all scoped to one sandbox instance. Kept small on purpose: every
extra tool is schema tokens on every request and one more way for the agent
to wander off the task.

`run_command` is the wide one. Under the Docker sandbox it is contained. Under
the local sandbox it runs as you -- see the warning in sandbox.py.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .sandbox import Sandbox

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "list_files",
        "description": (
            "List files in the repository matching a glob pattern, relative to "
            "the repo root. Use this to orient yourself before reading. "
            "Example patterns: '**/*.py', 'src/**/*.py', '*.toml'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "glob": {
                    "type": "string",
                    "description": "Glob pattern relative to the repo root.",
                }
            },
            "required": ["glob"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read a file from the repository. Returns the full contents with "
            "1-indexed line numbers prefixed. Read a file before editing it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to the repo root.",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Replace an exact string in a file. `old_str` must appear exactly "
            "once in the file, including whitespace and indentation, and must "
            "not include the line-number prefixes that read_file adds. This is "
            "the preferred way to change existing code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the repo root."},
                "old_str": {"type": "string", "description": "Exact text to replace."},
                "new_str": {"type": "string", "description": "Replacement text."},
            },
            "required": ["path", "old_str", "new_str"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write a file, creating it or overwriting it entirely. Use edit_file "
            "for changes to existing files; use this only for new files or full "
            "rewrites."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to the repo root."},
                "content": {"type": "string", "description": "Full file contents."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Run a shell command inside the sandbox, from the repo root, with "
            "the target Python environment active. Use it to check imports, run "
            "a single test, or inspect installed package versions. Returns exit "
            "code plus combined output. Do not run the full test suite -- the "
            "harness does that between your turns and reports the result."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Command to run, e.g. 'python -c \"import mypkg\"'.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds. Default 120.",
                },
            },
            "required": ["command"],
        },
    },
]


def _number_lines(text: str) -> str:
    lines = text.splitlines()
    width = len(str(len(lines))) or 1
    return "\n".join(f"{i:>{width}}\t{line}" for i, line in enumerate(lines, 1))


def build_dispatch(sandbox: Sandbox) -> Callable[[str, dict[str, Any]], tuple[str, bool]]:
    """Return a dispatcher bound to one sandbox.

    Returns (result_text, is_error) so the caller can mark the tool_result
    block appropriately -- the agent recovers much better from an explicit
    error than from a success-shaped message that happens to say "failed".
    """

    def dispatch(name: str, args: dict[str, Any]) -> tuple[str, bool]:
        try:
            if name == "list_files":
                matches = sandbox.list_files(args["glob"])
                if not matches:
                    return f"No files match {args['glob']!r}.", False
                head = matches[:400]
                out = "\n".join(head)
                if len(matches) > len(head):
                    out += f"\n... and {len(matches) - len(head)} more"
                return out, False

            if name == "read_file":
                return _number_lines(sandbox.read(args["path"])), False

            if name == "edit_file":
                path, old, new = args["path"], args["old_str"], args["new_str"]
                content = sandbox.read(path)
                count = content.count(old)
                if count == 0:
                    return (
                        f"No match for old_str in {path}. The file may differ from "
                        f"what you expect -- read it again before retrying.",
                        True,
                    )
                if count > 1:
                    return (
                        f"old_str appears {count} times in {path}; it must be unique. "
                        f"Include more surrounding context to disambiguate.",
                        True,
                    )
                sandbox.write(path, content.replace(old, new))
                return f"Edited {path}.", False

            if name == "write_file":
                sandbox.write(args["path"], args["content"])
                return f"Wrote {args['path']}.", False

            if name == "run_command":
                timeout = int(args.get("timeout") or 120)
                res = sandbox.exec(args["command"].split(), timeout=timeout)
                body = res.tail(4000) or "(no output)"
                return f"exit={res.returncode}\n{body}", False

            return f"Unknown tool: {name}", True

        except Exception as exc:  # surfaced to the agent so it can adapt
            return f"{type(exc).__name__}: {exc}", True

    return dispatch


def tool_arg_preview(args: dict[str, Any], limit: int = 160) -> str:
    """Compact rendering of tool inputs for the trace log."""
    text = json.dumps(args, default=str)
    return text if len(text) <= limit else text[:limit] + "..."
