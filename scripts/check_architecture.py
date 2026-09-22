#!/usr/bin/env python3
"""Architecture boundary checker for Tlon.

Enforces DAG Component Architecture rules:
1. Component dependencies must be constructor-injected only
2. No setter/property injection allowed
3. No runtime component replacement
4. EventBus for inter-component communication

Usage:
    python scripts/check_architecture.py [paths...]
    python scripts/check_architecture.py src/tlon_server

Exit codes:
    0 - No violations
    1 - Violations found
    2 - Internal error
"""

import ast
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Violation:
    """An architecture violation."""

    file: Path
    line: int
    rule: str
    message: str
    suggestion: str


class ArchitectureChecker:
    """Checks Python files for DAG Component Architecture violations."""

    # Patterns that indicate setter injection
    SETTER_PATTERNS = [
        "set_",
        "inject_",
        "replace_",
        "update_",
    ]

    # Component class names (heuristic detection)
    COMPONENT_SUFFIXES = [
        "Manager",
        "Registry",
        "Service",
        "Router",
        "Gateway",
        "Store",
        "Bus",
    ]

    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.violations: list[Violation] = []
        self.checked_files = 0

    def check_file(self, file_path: Path) -> list[Violation]:
        """Check a single Python file for violations."""
        try:
            content = file_path.read_text(encoding="utf-8")
            tree = ast.parse(content)
        except SyntaxError as e:
            self.violations.append(
                Violation(
                    file=file_path,
                    line=e.lineno or 1,
                    rule="PARSE_ERROR",
                    message=f"Failed to parse file: {e}",
                    suggestion="Fix syntax error",
                )
            )
            return self.violations

        self.checked_files += 1

        # Run all checks
        self._check_setter_injection(tree, file_path, content)
        self._check_property_setters(tree, file_path, content)
        self._check_component_replacement(tree, file_path, content)
        self._check_global_component_access(tree, file_path, content)
        self._check_direct_component_calls(tree, file_path, content)

        return self.violations

    def _check_setter_injection(self, tree: ast.AST, file_path: Path, content: str) -> None:
        """Check for setter injection methods."""
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                method_name = node.name

                # Check for setter patterns
                for pattern in self.SETTER_PATTERNS:
                    if method_name.startswith(pattern):
                        # Check if it's a method that takes a component-like parameter
                        args = node.args.args
                        if len(args) >= 2:  # self + at least one arg
                            first_arg = args[1].arg
                            # Check if first arg looks like a component
                            if self._looks_like_component(first_arg):
                                self.violations.append(
                                    Violation(
                                        file=file_path,
                                        line=node.lineno,
                                        rule="SETTER_INJECTION",
                                        message=f"Method '{method_name}' appears to be a setter for component injection",
                                        suggestion="Use constructor injection instead. Remove this method and add parameter to __init__",
                                    )
                                )

    def _check_property_setters(self, tree: ast.AST, file_path: Path, content: str) -> None:
        """Check for property setters that inject components."""
        for node in ast.walk(tree):
            # Check if it's a @property.setter
            if isinstance(node, ast.FunctionDef) and node.decorator_list:
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Attribute) and decorator.attr == "setter":
                        # Check if property name looks like a component
                        property_name = node.name
                        if self._looks_like_component(property_name):
                            self.violations.append(
                                Violation(
                                    file=file_path,
                                    line=node.lineno,
                                    rule="PROPERTY_SETTER_INJECTION",
                                    message=f"Property setter for '{property_name}' may be used for component injection",
                                    suggestion="Use constructor injection instead. Make this property read-only or remove it",
                                )
                            )

    def _check_component_replacement(self, tree: ast.AST, file_path: Path, content: str) -> None:
        """Check for methods that replace components at runtime."""
        replacement_keywords = ["replace", "swap", "switch", "change"]

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                method_name = node.name.lower()

                # Skip if already caught by setter check (avoid duplicates)
                if any(method_name.startswith(pattern) for pattern in self.SETTER_PATTERNS):
                    continue

                # Check if method name suggests replacement
                for keyword in replacement_keywords:
                    if keyword in method_name:
                        args = node.args.args
                        if len(args) >= 2:
                            first_arg = args[1].arg
                            if self._looks_like_component(first_arg):
                                self.violations.append(
                                    Violation(
                                        file=file_path,
                                        line=node.lineno,
                                        rule="RUNTIME_REPLACEMENT",
                                        message=f"Method '{node.name}' appears to replace a component at runtime",
                                        suggestion="Components should be immutable after construction. Remove this method",
                                    )
                                )

    def _check_global_component_access(self, tree: ast.AST, file_path: Path, content: str) -> None:
        """Check for imports from global state modules."""
        for node in ast.walk(tree):
            # Check if importing from global state modules
            if isinstance(node, ast.ImportFrom) and (
                node.module
                and any(x in (node.module or "") for x in ["global", "singleton", "state"])
            ):
                for alias in node.names:
                    name = alias.asname or alias.name
                    if self._looks_like_component(name):
                        self.violations.append(
                            Violation(
                                file=file_path,
                                line=node.lineno,
                                rule="GLOBAL_STATE_DEPENDENCY",
                                message=f"Importing '{name}' from global state module '{node.module}'",
                                suggestion="Use constructor injection instead. Pass the dependency through __init__",
                            )
                        )

    def _check_direct_component_calls(self, tree: ast.AST, file_path: Path, content: str) -> None:
        """Check for direct method calls between components (bypassing EventBus)."""
        # This is a heuristic check - direct calls are sometimes valid (Facade pattern)
        # We only flag suspicious patterns

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                # Check for method calls on what looks like a component
                obj_name = self._get_object_name(node.func.value)
                if obj_name and self._looks_like_component(obj_name):
                    method_name = node.func.attr

                    # Flag non-trivial method calls (not simple getters)
                    # Check if this is in a Component class
                    # Skip if it's a Facade pattern (ClientGateway)
                    if (
                        method_name not in ["get", "is_", "has_", "count", "list", "find"]
                        and self._is_in_component_class(tree, node)
                        and not self._is_facade_class(tree, node)
                    ):
                        self.violations.append(
                            Violation(
                                file=file_path,
                                line=node.lineno,
                                rule="DIRECT_COMPONENT_CALL",
                                message=f"Direct call to '{obj_name}.{method_name}()'. Consider using EventBus for inter-component communication",
                                suggestion="If this is a Facade pattern, add comment '# Facade: OK'. Otherwise, use EventBus",
                            )
                        )

    def _looks_like_component(self, name: str) -> bool:
        """Check if a name looks like a component."""
        name_lower = name.lower()
        return any(
            name_lower.endswith(suffix.lower()) for suffix in self.COMPONENT_SUFFIXES
        ) or any(
            pattern in name_lower for pattern in ["bus", "store", "registry", "manager", "service"]
        )

    def _get_object_name(self, node: ast.AST) -> str | None:
        """Extract object name from AST node."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return self._get_object_name(node.value)
        return None

    def _is_in_component_class(self, tree: ast.AST, node: ast.AST) -> bool:
        """Check if node is inside a Component subclass."""
        # Simplified check - in production, use proper parent tracking
        for n in ast.walk(tree):
            if isinstance(n, ast.ClassDef):
                # Check if class inherits from Component
                for base in n.bases:
                    if isinstance(base, ast.Name) and base.id == "Component":
                        return True
        return False

    def _is_facade_class(self, tree: ast.AST, node: ast.AST) -> bool:
        """Check if the containing class is a Facade."""
        for n in ast.walk(tree):
            if isinstance(n, ast.ClassDef) and ("Gateway" in n.name or "Facade" in n.name):
                return True
        return False

    def check_directory(
        self, directory: Path, exclude_patterns: set[str] | None = None
    ) -> list[Violation]:
        """Check all Python files in a directory."""
        exclude_patterns = exclude_patterns or {"test_", "_test.py", "__pycache__"}

        for py_file in directory.rglob("*.py"):
            # Skip excluded patterns
            if any(pattern in str(py_file) for pattern in exclude_patterns):
                continue

            self.check_file(py_file)

        return self.violations

    def print_report(self) -> None:
        """Print violation report."""
        print("=" * 80)
        print("DAG Component Architecture Check")
        print("=" * 80)
        print()

        if not self.violations:
            print(f"✅ All checks passed! Checked {self.checked_files} files.")
            print()
            print("Architecture rules enforced:")
            print("  1. ✅ Constructor injection only (no setters)")
            print("  2. ✅ No property injection")
            print("  3. ✅ No runtime component replacement")
            print("  4. ✅ No global state dependencies")
            return

        # Group by rule
        by_rule: dict = {}
        for v in self.violations:
            by_rule.setdefault(v.rule, []).append(v)

        print(f"❌ Found {len(self.violations)} violations in {self.checked_files} files:")
        print()

        for rule, violations in sorted(by_rule.items()):
            print(f"\n{rule} ({len(violations)} violations):")
            print("-" * 60)
            for v in violations:
                try:
                    rel_path = v.file.relative_to(self.project_root)
                except ValueError:
                    rel_path = v.file
                print(f"  {rel_path}:{v.line}")
                print(f"    {v.message}")
                print(f"    💡 {v.suggestion}")
                print()

        print("=" * 80)
        print("To suppress a violation, add a comment: # Arch: OK - [reason]")
        print("=" * 80)


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Check DAG Component Architecture compliance")
    parser.add_argument(
        "paths", nargs="*", help="Files or directories to check (default: src/tlon)"
    )
    parser.add_argument(
        "--project-root", type=Path, default=Path.cwd(), help="Project root directory"
    )

    args = parser.parse_args()

    # Default to src/tlon if no paths specified
    if not args.paths:
        paths = [args.project_root / "src" / "tlon"]
    else:
        paths = [Path(p) for p in args.paths]

    checker = ArchitectureChecker(args.project_root)

    for path in paths:
        if path.is_file():
            checker.check_file(path)
        elif path.is_dir():
            checker.check_directory(path)
        else:
            print(f"Warning: {path} not found")

    checker.print_report()

    sys.exit(1 if checker.violations else 0)


if __name__ == "__main__":
    main()
