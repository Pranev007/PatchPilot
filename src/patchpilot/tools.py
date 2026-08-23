"""Tool surface exposed to the agent.

Three tools -- list, read, edit -- all scoped to one sandbox instance.

It used to be five. `write_file` and `run_command` were removed after the
failure traces showed what the agent did with them: together they make an
ad-hoc REPL, and once the agent starts using them that way it explores
instead of editing. Every repository that failed spent 40-62% of its calls on
`run_command` and wrote five to eight scratch files it later deleted; every
repository that succeeded used neither, and finished in 2 to 48 calls.

Narrowing the surface is also the only fix that addresses the cause. Telling
the agent to use the shell less in the prompt helped (63 calls down to 27 on
one repo) but did not stop it, because a tool that exists will be used. A
3.8 -> 3.12 migration is edits to pins, metadata and deprecated calls; none of
it needs a shell, and the harness already reports the test results a shell
would be used to gather.
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
            "Read a file from the repository, with 1-indexed line numbers "
            "prefixed. Read a file before editing it. Large files are "
            "truncated; pass `offset` and `limit` to page through one instead "
            "of pulling the whole thing into context."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relative to the repo root.",
                },
                "offset": {
                    "type": "integer",
                    "description": "1-indexed line to start from. Default 1.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Lines to return. Default {400}.",
                },
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
]


# Files longer than this are truncated. This is cost control, not tidiness:
# anything read stays in the conversation for the rest of the repo and is
# re-sent on every later turn, so one 13,000-character changelog pulled in
# during exploration gets paid for dozens of times. Cache reads were 48% of
# the first calibration run's bill.
_READ_LIMIT = 400


def _read_window(text: str, offset: int = 1, limit: int = _READ_LIMIT) -> str:
    """Return a numbered slice of a file, stating what was left out."""
    lines = text.splitlines()
    total = len(lines)
    start = min(max(offset, 1), total + 1)
    window = lines[start - 1 : start - 1 + max(limit, 1)]
    width = len(str(start + len(window))) or 1
    body = "\n".join(f"{i:>{width}}\t{line}" for i, line in enumerate(window, start))
    end = start + len(window) - 1
    if start > 1 or end < total:
        body += (
            f"\n\n[showing lines {start}-{end} of {total}. "
            "Use offset/limit to read another range.]"
        )
    return body


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
                offset = max(int(args.get("offset") or 1), 1)
                limit = int(args.get("limit") or _READ_LIMIT)
                return _read_window(sandbox.read(args["path"]), offset, limit), False

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

            return f"Unknown tool: {name}", True

        except Exception as exc:  # surfaced to the agent so it can adapt
            return f"{type(exc).__name__}: {exc}", True

    return dispatch


def tool_arg_preview(args: dict[str, Any], limit: int = 160) -> str:
    """Compact rendering of tool inputs for the trace log."""
    text = json.dumps(args, default=str)
    return text if len(text) <= limit else text[:limit] + "..."
