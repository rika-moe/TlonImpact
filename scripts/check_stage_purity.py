#!/usr/bin/env python3
"""Lint: stages must stay pure — no direct session mutation (C2).

Stage modules under src/tlon/agents/stages/ are pure transformations
``Session x Services -> StageOutcome``: every session-state change is
declared in the returned StageOutcome and applied by the engine
(LoopDriver._apply_outcome). This script forbids, inside stage modules:

1. Calls to session mutator methods (add_tool_result,
   set_last_llm_response, mark_active/waiting/completed,
   advance_iteration, record_error, dispatch_tools, collect_completed,
   cancel_pending) — declare the change in the StageOutcome instead;
   the harvest (collect_completed) and dispatch are the engine's.
2. Direct attribute assignment on the session (``session.x = ...``).

Reads (session.context, session.iteration, ...) and observability
through StageServices stay fine.

Usage:
    python scripts/check_stage_purity.py [paths...]
    python scripts/check_stage_purity.py --ci

Exit codes:
    0 - No violations
    1 - Violations found
"""

import argparse
import ast
import sys
from pathlib import Path

STAGES_DIR = Path("src/tlon/agents/stages")

FORBIDDEN_METHODS = {
    "add_tool_result",
    "set_last_llm_response",
    "mark_active",
    "mark_waiting",
    "mark_completed",
    "advance_iteration",
    "record_error",
    "dispatch_tools",
    "collect_completed",
    "cancel_pending",
}


def _is_session(node: ast.AST) -> bool:
    """True if the node is the ``session`` parameter (name heuristic)."""
    return isinstance(node, ast.Name) and node.id == "session"


def check_file(file_path: Path) -> list[tuple[int, str]]:
    """Return list of (line, message) violations."""
    violations = []
    try:
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content)
    except SyntaxError as e:
        return [(e.lineno or 1, f"Syntax error: {e}")]

    for node in ast.walk(tree):
        # session.<mutator>(...)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and _is_session(node.func.value)
            and node.func.attr in FORBIDDEN_METHODS
        ):
            violations.append(
                (
                    node.lineno,
                    f"Stage calls session.{node.func.attr}() directly; "
                    "declare the change in StageOutcome and let the engine apply it (C2)",
                )
            )
        # session.<attr> = ...
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and _is_session(target.value):
                    violations.append(
                        (
                            node.lineno,
                            f"Stage assigns session.{target.attr} directly; "
                            "declare the change in StageOutcome and let the engine apply it (C2)",
                        )
                    )

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Ban direct session mutation in stages (C2)")
    parser.add_argument(
        "paths",
        nargs="*",
        help=f"Files or directories to check (default: {STAGES_DIR})",
    )
    parser.add_argument(
        "--ci", action="store_true", help="Exit with error code if violations found"
    )
    args = parser.parse_args()

    project_root = Path.cwd()
    paths = [project_root / STAGES_DIR] if not args.paths else [Path(p) for p in args.paths]

    all_violations: list[tuple[Path, int, str]] = []
    checked_files = 0

    for path in paths:
        files = [path] if path.is_file() else sorted(path.rglob("*.py"))
        for py_file in files:
            if py_file.name == "__init__.py" or "__pycache__" in py_file.parts:
                continue
            checked_files += 1
            for line, msg in check_file(py_file):
                all_violations.append((py_file, line, msg))

    if not all_violations:
        print(f"✅ No stage-purity violations found in {checked_files} stage files.")
        return 0

    print(f"❌ Found {len(all_violations)} violations in {checked_files} stage files:\n")
    for file_path, line, msg in all_violations:
        try:
            rel = file_path.relative_to(project_root)
        except ValueError:
            rel = file_path
        print(f"  {rel}:{line}")
        print(f"    {msg}")
    print()
    return 1


if __name__ == "__main__":
    sys.exit(main())
