#!/usr/bin/env python3
"""Device and EventBus compliance checker.

Checks Tlon codebase for proper Device usage patterns.
"""

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

# Rule definitions
RULES = {
    "E001": ("DEVICE_MISSING_INIT", "error", "Device subclass must call Device.__init__()"),
    "E002": (
        "DEVICE_DIRECT_SUBSCRIBE",
        "error",
        "Use self.subscribe() instead of event_bus.subscribe()",
    ),
    "E003": (
        "MIXED_SHUTDOWN_PATTERN",
        "error",
        "Must call unsubscribe_all() in shutdown() when inheriting both Component and Device",
    ),
    "E004": (
        "DEVICE_INHERIT_COMPONENT",
        "error",
        "Device should NOT inherit Component - they are orthogonal concepts",
    ),
    "W001": (
        "MISSING_UNSUBSCRIBE_ALL",
        "warning",
        "Device subclass should call unsubscribe_all() in shutdown()",
    ),
    "W002": (
        "COMPONENT_DEVICE_INIT_ORDER",
        "warning",
        "Call Component.__init__ before Device.__init__ in multiple inheritance",
    ),
    "I001": (
        "DEVICE_WITHOUT_NAME",
        "info",
        "Consider providing explicit name to Device constructor",
    ),
}


@dataclass
class Violation:
    rule_id: str
    file: Path
    line: int
    message: str


class DeviceChecker(ast.NodeVisitor):
    """AST visitor to check Device usage patterns."""

    def __init__(self, file_path: Path):
        self.file_path = file_path
        self.violations: list[Violation] = []
        self.current_class: ast.ClassDef | None = None
        self.is_device_subclass = False
        self.is_component_subclass = False
        self.has_device_init = False
        self.has_component_init = False
        self.init_order: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Check class inheritance patterns."""
        previous_class = self.current_class
        previous_is_device = self.is_device_subclass
        previous_is_component = self.is_component_subclass

        self.current_class = node
        self.is_device_subclass = self._inherits_from(node, "Device")
        self.is_component_subclass = self._inherits_from(node, "Component")

        # Check E004: Device should not inherit Component
        base_names = [self._get_name(b) for b in node.bases]
        if "Device" in base_names and "Component" in base_names and node.name == "Device":
            # This is the base Device class definition itself - check its bases
            pass  # Will check below

        # If this IS the Device class definition (not a subclass)
        if (
            node.name == "Device"
            and self._is_device_definition(node)
            and self._inherits_from(node, "Component")
        ):
            self.violations.append(
                Violation(
                    "E004",
                    self.file_path,
                    node.lineno,
                    "Device class should NOT inherit Component",
                )
            )

        # Check multiple inheritance pattern
        if self.is_device_subclass and self.is_component_subclass:
            # Both Component and Device - valid pattern, check init order later
            pass

        # Visit class body
        for item in node.body:
            self.visit(item)

        # Check W002: Component should come before Device in MRO (init order)
        if (
            self.is_device_subclass
            and self.is_component_subclass
            and self.init_order
            and "Device" in self.init_order
            and "Component" in self.init_order
        ):
            device_idx = self.init_order.index("Device")
            comp_idx = self.init_order.index("Component")
            if device_idx < comp_idx:
                self.violations.append(
                    Violation(
                        "W002",
                        self.file_path,
                        self._find_init_method_line(node),
                        "Call Component.__init__ before Device.__init__",
                    )
                )

            # Check E003: shutdown must call unsubscribe_all
            shutdown_method = self._find_method(node, "shutdown")
            if shutdown_method and not self._calls_unsubscribe_all(shutdown_method):
                self.violations.append(
                    Violation(
                        "E003",
                        self.file_path,
                        shutdown_method.lineno,
                        "shutdown() must call self.unsubscribe_all()",
                    )
                )
            elif not shutdown_method:
                # W001: missing shutdown
                self.violations.append(
                    Violation(
                        "W001",
                        self.file_path,
                        node.lineno,
                        f"Class {node.name} should implement shutdown() with unsubscribe_all()",
                    )
                )

        # Check E001: Device subclass must call Device.__init__
        if self.is_device_subclass and not self._is_device_definition(node):
            init_method = self._find_method(node, "__init__")
            if init_method and not self.has_device_init:
                self.violations.append(
                    Violation(
                        "E001",
                        self.file_path,
                        init_method.lineno,
                        f"Class {node.name} must call Device.__init__()",
                    )
                )
            # If no __init__ defined - inherits parent's, which might be okay

        self.current_class = previous_class
        self.is_device_subclass = previous_is_device
        self.is_component_subclass = previous_is_component
        self.has_device_init = False
        self.has_component_init = False
        self.init_order = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Check method implementations."""
        if not self.current_class:
            return

        if node.name == "__init__":
            self._check_init_method(node)
        elif node.name == "shutdown":
            self._check_shutdown_method(node)

        # Check E002: direct event_bus.subscribe() calls
        self._check_direct_subscribe(node)

        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Check async method implementations."""
        self.visit_FunctionDef(node)  # Same logic

    def _check_init_method(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Check __init__ for proper parent class initialization."""
        for stmt in ast.walk(node):
            if isinstance(stmt, ast.Call):
                func_name = self._get_call_name(stmt)
                if func_name == "Device.__init__":
                    self.has_device_init = True
                    self.init_order.append("Device")
                    # Check I001: explicit name parameter
                    if not self._has_keyword_arg(stmt, "name"):
                        self.violations.append(
                            Violation(
                                "I001",
                                self.file_path,
                                stmt.lineno,
                                "Consider providing explicit name to Device constructor",
                            )
                        )
                elif func_name == "Component.__init__":
                    self.has_component_init = True
                    self.init_order.append("Component")

    def _check_shutdown_method(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Check shutdown for unsubscribe_all call."""
        if self.is_device_subclass and self._calls_unsubscribe_all(node):
            self.has_unsubscribe_all = True

    def _check_direct_subscribe(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Check for direct event_bus.subscribe() calls (E002)."""
        for stmt in ast.walk(node):
            if (
                isinstance(stmt, ast.Call)
                and isinstance(stmt.func, ast.Attribute)
                and isinstance(stmt.func.value, ast.Name)
                and stmt.func.value.id != "self"
            ):
                func_name = self._get_call_name(stmt)
                if "event_bus.subscribe" in func_name or func_name == "subscribe":
                    self.violations.append(
                        Violation(
                            "E002",
                            self.file_path,
                            stmt.lineno,
                            f"Use self.subscribe() instead of {stmt.func.value.id}.subscribe()",
                        )
                    )

    def _calls_unsubscribe_all(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        """Check if method calls unsubscribe_all."""
        for stmt in ast.walk(node):
            if isinstance(stmt, ast.Call):
                func_name = self._get_call_name(stmt)
                if "unsubscribe_all" in func_name:
                    return True
        return False

    def _find_init_method_line(self, class_node: ast.ClassDef) -> int:
        """Find line number of __init__ method."""
        for item in class_node.body:
            if (
                isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name == "__init__"
            ):
                return item.lineno
        return class_node.lineno

    def _find_method(
        self, class_node: ast.ClassDef, method_name: str
    ) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        """Find a method in class by name."""
        for item in class_node.body:
            if (
                isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                and item.name == method_name
            ):
                return item
        return None

    def _inherits_from(self, node: ast.ClassDef, base_name: str) -> bool:
        """Check if class inherits from a specific base class."""
        for base in node.bases:
            name = self._get_name(base)
            if name == base_name:
                return True
        return False

    def _is_device_definition(self, node: ast.ClassDef) -> bool:
        """Check if this is the Device class definition (not a subclass)."""
        # Heuristic: if file path contains device.py and class name is Device
        return node.name == "Device" and "device.py" in str(self.file_path)

    def _get_name(self, node: ast.AST) -> str:
        """Get name from AST node."""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return f"{self._get_name(node.value)}.{node.attr}"
        return ""

    def _get_call_name(self, node: ast.Call) -> str:
        """Get the name of a function call."""
        return self._get_name(node.func)

    def _has_keyword_arg(self, node: ast.Call, arg_name: str) -> bool:
        """Check if call has a specific keyword argument."""
        return any(kw.arg == arg_name for kw in node.keywords)


def check_file(file_path: Path) -> list[Violation]:
    """Check a single Python file for Device compliance."""
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except SyntaxError as e:
        return [Violation("SYNTAX", file_path, e.lineno or 1, str(e))]
    except Exception as e:
        return [Violation("ERROR", file_path, 1, str(e))]

    checker = DeviceChecker(file_path)
    checker.visit(tree)
    return checker.violations


def main() -> int:
    """Main entry point."""
    project_root = Path(__file__).parent.parent
    src_dir = project_root / "src" / "tlon"

    if not src_dir.exists():
        print(f"Error: Source directory not found: {src_dir}")
        return 1

    all_violations: list[Violation] = []

    # Scan all Python files
    for py_file in src_dir.rglob("*.py"):
        # Skip tests for some rules, but check them too
        violations = check_file(py_file)
        all_violations.extend(violations)

    # Group by severity
    errors = [v for v in all_violations if RULES.get(v.rule_id, ("", "error", ""))[1] == "error"]
    warnings = [
        v for v in all_violations if RULES.get(v.rule_id, ("", "warning", ""))[1] == "warning"
    ]
    infos = [v for v in all_violations if RULES.get(v.rule_id, ("", "info", ""))[1] == "info"]

    # Print results
    for v in errors:
        rule_name, _, description = RULES.get(v.rule_id, (v.rule_id, "error", ""))
        print(f"❌ {v.file}:{v.line}: {rule_name} - {description}")

    for v in warnings:
        rule_name, _, description = RULES.get(v.rule_id, (v.rule_id, "warning", ""))
        print(f"⚠️  {v.file}:{v.line}: {rule_name} - {description}")

    for v in infos:
        rule_name, _, description = RULES.get(v.rule_id, (v.rule_id, "info", ""))
        print(f"ℹ️  {v.file}:{v.line}: {rule_name} - {description}")

    # Summary
    print(f"\n{'=' * 60}")
    print(f"Total: {len(errors)} errors, {len(warnings)} warnings, {len(infos)} info")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
