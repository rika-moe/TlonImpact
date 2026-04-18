#!/usr/bin/env python3
"""Lint: ban re-exports in __init__.py files.

In __init__.py, relative imports (from .xxx import) and absolute imports
from the project package (from tlon.xxx import) are forbidden.

This prevents IDE rename-refactor from silently breaking across package
boundaries, since re-exported names are invisible to most static analysis.

Usage:
    python scripts/check_no_reexport.py [paths...]
    python scripts/check_no_reexport.py --ci

Exit codes:
    0 - No violations
    1 - Violations found
"""

import argparse
import ast
import sys
from pathlib import Path


def find_root_package(src_dir: Path) -> str | None:
    """Auto-detect root package name from src/ directory."""
    if not src_dir.exists():
        return None
    candidates = [
        p.name
        for p in src_dir.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name != "tests"
    ]
    return candidates[0] if len(candidates) == 1 else None


def check_file(file_path: Path, root_package: str) -> list[tuple[int, str]]:
    """Return list of (line, message) violations."""
    violations = []
    try:
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content)
    except SyntaxError as e:
        return [(e.lineno or 1, f"Syntax error: {e}")]

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue

        # Allow __future__ imports unconditionally
        if node.module == "__future__":
            continue

        # Ban relative imports (from .module import ...)
        if node.level > 0:
            module = "." * node.level + (node.module or "")
            violations.append(
                (
                    node.lineno,
                    f"Relative import is forbidden in __init__.py: from {module} import ...",
                )
            )
            continue

        # Ban absolute imports from the project package (from tlon.xxx import ...)
        if node.module and node.module.split(".")[0] == root_package:
            violations.append(
                (
                    node.lineno,
                    f"Re-export from project package is forbidden in __init__.py: from {node.module} import ...",
                )
            )

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Ban re-exports in __init__.py")
    parser.add_argument("paths", nargs="*", help="Files or directories to check")
    parser.add_argument(
        "--ci", action="store_true", help="Exit with error code if violations found"
    )
    parser.add_argument(
        "--root-package", default=None, help="Root package name (auto-detected by default)"
    )
    args = parser.parse_args()

    project_root = Path.cwd()
    src_dir = project_root / "src"
    root_package = args.root_package or find_root_package(src_dir)
    if not root_package:
        print("Could not auto-detect root package. Please specify --root-package.")
        return 2

    paths = [src_dir] if not args.paths else [Path(p) for p in args.paths]

    all_violations: list[tuple[Path, int, str]] = []
    checked_files = 0

    for path in paths:
        if path.is_file():
            if path.name == "__init__.py":
                checked_files += 1
                for line, msg in check_file(path, root_package):
                    all_violations.append((path, line, msg))
        elif path.is_dir():
            for init_file in path.rglob("__init__.py"):
                checked_files += 1
                for line, msg in check_file(init_file, root_package):
                    all_violations.append((init_file, line, msg))
        else:
            print(f"Warning: {path} not found")

    if not all_violations:
        print(f"✅ No re-export violations found in {checked_files} __init__.py files.")
        return 0

    print(f"❌ Found {len(all_violations)} violations in {checked_files} __init__.py files:\n")
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
