"""Generate the mechanically-derivable sections of docs/reference/ from source.

Inspired by deepseek-harness's doc generation + freshness gate.
Handwritten narrative stays
handwritten; only the drift-prone inventories are generated:

- ``tool-registry`` in docs/reference/tools-reference.md — the
  member tool inventory from the shared operation definitions;
  domain tables use those same input and output schemas.
- ``event-types`` in docs/reference/api-reference.md — the flat EventType
  enum table from base/events/event.py (wire value + trailing comment).
- ``dsl-special-forms`` / ``dsl-builtins-core`` / ``dsl-builtins-arithmetic``
  in docs/reference/tlosure-reference.md — the language-level whitelists
  (special forms from tlosure/eval.py, the general ``CORE_BUILTINS`` and
  ``ARITHMETIC_BUILTINS`` vocabularies from tlosure/builtins.py).
- ``dsl-on-events`` / ``dsl-builtins-pure`` /
  ``dsl-fuel-defaults`` / ``bridge-query-ports`` in dsl-reference.md —
  event names, ordinary vocabularies, resource bounds and explicit query ports.
- ``rest-endpoints`` / ``mode-patterns`` in docs/reference/api-reference.md —
  the route table from ``RESTAPIHandler.create_app`` (with registration
  conditions) and the ``MODE_EVENT_PATTERNS`` mode semantics.
- ``agent-yaml-fields-*`` / ``env-vars`` in docs/reference/config-reference.md —
  the pydantic model fields of base/config.py and the ``os.getenv`` calls
  of base/settings.py.

Generated blocks live between markers in the target documents::

    <!-- BEGIN GENERATED: <section> -->
    ...
    <!-- END GENERATED: <section> -->

Usage:
    uv run python scripts/gen_reference_docs.py          # regenerate in place
    uv run python scripts/gen_reference_docs.py --check  # freshness gate (CI)
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tlon.operations.context import member_context_operation
from tlon.operations.core import Operation
from tlon.operations.evolution import evolution_operation_definitions
from tlon.operations.filesystem import filesystem_operation_definitions
from tlon.operations.identity import identity_operation_definitions
from tlon.operations.lifecycle import lifecycle_operation_definitions
from tlon.operations.mail import mail_operation_definitions
from tlon.operations.model import model_operation_definitions
from tlon.operations.observability import observability_operation_definitions
from tlon.operations.registry import make_registry_query_operations
from tlon.operations.runs import run_operation_definitions, run_read_operation_definitions
from tlon.operations.shell import shell_operation_definitions
from tlon.operations.state import state_operation_definitions
from tlon.operations.tasks import task_operation_definitions
from tlon.operations.time import time_operation_definitions

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src" / "tlon"
TLOSURE_SRC = REPO_ROOT / "src" / "tlosure"
SERVER_SRC = REPO_ROOT / "src" / "tlon_server"
EVENTS_FILE = SRC / "base" / "events" / "event.py"
EVAL_FILE = TLOSURE_SRC / "eval.py"
BUILTINS_FILE = TLOSURE_SRC / "builtins.py"
VOCABULARY_FILE = SRC / "agents" / "vocabulary.py"
MEMBER_SPEC_FILE = SRC / "agents" / "member_spec.py"
REST_FILE = SERVER_SRC / "api" / "rest" / "app.py"
NOTIFICATION_FILE = SERVER_SRC / "core" / "notification_service.py"
CONFIG_FILE = SRC / "base" / "config.py"
SETTINGS_FILE = SRC / "base" / "settings.py"
TOOLS_REF = REPO_ROOT / "docs" / "reference" / "tools-reference.md"
API_REF = REPO_ROOT / "docs" / "reference" / "api-reference.md"
DSL_REF = REPO_ROOT / "docs" / "reference" / "dsl-reference.md"
TLOSURE_REF = REPO_ROOT / "docs" / "reference" / "tlosure-reference.md"
CONFIG_REF = REPO_ROOT / "docs" / "reference" / "config-reference.md"

GENERATED_NOTE = (
    "（本区块由 `scripts/gen_reference_docs.py` 生成，勿手改；改了会被 `--check` 打回）"
)


class GenerationError(RuntimeError):
    """A source construct the generator relies on could not be extracted."""


# ==================== AST extraction ====================


def _literal(node: ast.expr) -> Any:
    """Evaluate a literal-only AST expression (Constants, containers, string
    concatenation). Anything else is a loud failure — generated docs must
    never silently guess."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _literal(node.left) + _literal(node.right)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_literal(node.operand)
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_literal(elt) for elt in node.elts]
    if isinstance(node, ast.Dict):
        return {
            _literal(k): _literal(v)
            for k, v in zip(node.keys, node.values, strict=True)
            if k is not None
        }
    raise GenerationError(f"non-literal expression at line {node.lineno}: {ast.dump(node)[:120]}")


@dataclass(frozen=True)
class EventInfo:
    member: str
    value: str
    comment: str


def event_types() -> list[EventInfo]:
    """Flat EventType table: member name, wire value, trailing comment."""
    source = EVENTS_FILE.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(EVENTS_FILE))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "EventType":
            events: list[EventInfo] = []
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.Assign)
                    and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                ):
                    value = _literal(stmt.value)
                    line = lines[stmt.lineno - 1]
                    comment = line.split("#", 1)[1].strip() if "#" in line else ""
                    events.append(
                        EventInfo(member=stmt.targets[0].id, value=str(value), comment=comment)
                    )
            return events
    raise GenerationError(f"{EVENTS_FILE}: class EventType not found")


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _module_value(tree: ast.Module, name: str, path: Path) -> ast.expr:
    """The value of a module-level ``name = ...`` / ``name: T = ...``."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return node.value
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and node.value is not None
        ):
            return node.value
    raise GenerationError(f"{path}: module-level assignment {name} not found")


def _frozenset_strings(path: Path, name: str) -> list[str]:
    """Members of a ``name = frozenset({...})`` literal, in source order."""
    value = _module_value(_parse(path), name, path)
    if not (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "frozenset"
        and len(value.args) == 1
        and isinstance(value.args[0], ast.Set)
    ):
        raise GenerationError(f"{path}: {name} is not a frozenset({{...}}) literal")
    return [str(_literal(elt)) for elt in value.args[0].elts]


def _function_doc_first_line(fn_name: str, sources: list[tuple[ast.Module, Path]]) -> str:
    """First docstring line of a module-level function ("" when undocumented).

    Searches ``sources`` in order: a registry may reference functions
    defined in another module (the host vocabulary in
    ``agents/vocabulary.py`` reuses tlosure's general functions).
    """
    for tree, _path in sources:
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_name:
                doc = ast.get_docstring(node)
                return doc.splitlines()[0] if doc else ""
    paths = ", ".join(str(path) for _tree, path in sources)
    raise GenerationError(f"function {fn_name} not found in {paths}")


@dataclass(frozen=True)
class BuiltinEntry:
    name: str
    pure: bool
    doc: str


def builtin_registry(name: str, path: Path) -> list[BuiltinEntry]:
    """A ``NAME: dict[str, Builtin] = {"x": Builtin(_fn, pure=...)}`` registry
    from ``path``, in source order. Function docstrings are looked up in
    ``path`` first, then in tlosure/builtins.py (the host vocabulary
    reuses the general functions defined there)."""
    tree = _parse(path)
    doc_sources = [(tree, path), (_parse(BUILTINS_FILE), BUILTINS_FILE)]
    value = _module_value(tree, name, path)
    if not isinstance(value, ast.Dict):
        raise GenerationError(f"{path}: {name} is not a dict literal")
    entries: list[BuiltinEntry] = []
    for key, val in zip(value.keys, value.values, strict=True):
        if key is None:
            # ``**OTHER_REGISTRY`` unpacking (e.g. DERIVED_BUILTINS =
            # {**CORE_BUILTINS, **ARITHMETIC_BUILTINS}): expand the named
            # registry's entries inline, resolved in ``path`` first, then
            # in tlosure/builtins.py.
            if not isinstance(val, ast.Name):
                raise GenerationError(f"{path}: {name} uses non-name dict unpacking")
            entries.extend(_resolve_unpacked_registry(val.id, path))
            continue
        builtin_name = str(_literal(key))
        if not (
            isinstance(val, ast.Call)
            and isinstance(val.func, ast.Name)
            and val.func.id == "Builtin"
            and val.args
            and isinstance(val.args[0], ast.Name)
        ):
            raise GenerationError(f"{path}: {name}[{builtin_name!r}] is not Builtin(<fn>, ...)")
        fn_name = val.args[0].id
        pure = True
        for kw in val.keywords:
            if kw.arg == "pure":
                pure = bool(_literal(kw.value))
            else:
                raise GenerationError(
                    f"{path}: {name}[{builtin_name!r}] has unexpected kwarg {kw.arg}"
                )
        entries.append(
            BuiltinEntry(
                name=builtin_name,
                pure=pure,
                doc=_function_doc_first_line(fn_name, doc_sources),
            )
        )
    if not entries:
        raise GenerationError(f"{path}: {name} is empty")
    return entries


def _resolve_unpacked_registry(name: str, path: Path) -> list[BuiltinEntry]:
    """The entries of a registry named in a ``**NAME`` unpacking, looked up
    in ``path`` first, then in tlosure/builtins.py."""
    for candidate in (path, BUILTINS_FILE):
        try:
            _module_value(_parse(candidate), name, candidate)
        except GenerationError:
            continue
        return builtin_registry(name, candidate)
    raise GenerationError(f"{path}: unpacks unknown registry {name!r}")


@dataclass(frozen=True)
class EndpointInfo:
    method: str
    path: str
    handler: str
    condition: str
    lineno: int


_ROUTER_METHODS = {
    "add_get": "GET",
    "add_post": "POST",
    "add_put": "PUT",
    "add_patch": "PATCH",
    "add_delete": "DELETE",
    "add_options": "OPTIONS",
    "add_static": "GET",
}


def _route_condition(test: ast.expr) -> str:
    """Label one enclosing ``if`` guard of a route registration."""
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
        return " + ".join(_route_condition(value) for value in test.values)
    if (
        isinstance(test, ast.Attribute)
        and isinstance(test.value, ast.Name)
        and test.value.id == "self"
        and test.attr
        in (
            "configs",
            "observability",
            "debug",
            "github_ingress",
            "github_webhook_secret",
        )
    ):
        return test.attr
    if (
        isinstance(test, ast.Call)
        and isinstance(test.func, ast.Attribute)
        and test.func.attr == "is_file"
    ):
        return "webui dist 存在"
    raise GenerationError(
        f"{REST_FILE}:{test.lineno} unrecognized route-guard condition: "
        f"{ast.dump(test)[:120]} — teach the generator"
    )


def rest_endpoints() -> list[EndpointInfo]:
    """Route registrations of ``RESTAPIHandler.create_app``, in source order."""
    tree = _parse(REST_FILE)
    fn = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "create_app"
        ),
        None,
    )
    if fn is None:
        raise GenerationError(f"{REST_FILE}: create_app not found")
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(fn):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    found: list[EndpointInfo] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Attribute)
            and func.value.attr == "router"
            and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "app"
        ):
            continue
        method_attr = func.attr
        if not method_attr.startswith("add_"):
            continue
        if method_attr not in _ROUTER_METHODS:
            raise GenerationError(f"{REST_FILE}:{node.lineno} unknown router method {method_attr}")
        if len(node.args) < 2:
            raise GenerationError(f"{REST_FILE}:{node.lineno} {method_attr} with < 2 args")
        path = _literal(node.args[0])
        if not isinstance(path, str):
            raise GenerationError(f"{REST_FILE}:{node.lineno} route path is not a string")
        handler_arg = node.args[1]
        if method_attr == "add_static":
            handler = "(static)"
        elif (
            isinstance(handler_arg, ast.Attribute)
            and isinstance(handler_arg.value, ast.Name)
            and handler_arg.value.id == "self"
        ):
            handler = handler_arg.attr
        else:
            raise GenerationError(
                f"{REST_FILE}:{node.lineno} {method_attr} handler is not self.<method>"
            )
        conditions: list[str] = []
        child: ast.AST = node
        parent = parents.get(id(child))
        while parent is not None and parent is not fn:
            if isinstance(parent, ast.If):
                label = _route_condition(parent.test)
                conditions.append(label if child in parent.body else f"非 {label}")
            elif isinstance(
                parent,
                (
                    ast.For,
                    ast.AsyncFor,
                    ast.While,
                    ast.With,
                    ast.AsyncWith,
                    ast.Try,
                    ast.FunctionDef,
                    ast.AsyncFunctionDef,
                ),
            ):
                raise GenerationError(
                    f"{REST_FILE}:{node.lineno} route registered inside an "
                    "unsupported construct — teach the generator"
                )
            child = parent
            parent = parents.get(id(child))
        condition = " + ".join(reversed(conditions)) if conditions else "无条件"
        found.append(
            EndpointInfo(
                method=_ROUTER_METHODS[method_attr],
                path=path,
                handler=handler,
                condition=condition,
                lineno=node.lineno,
            )
        )
    if not found:
        raise GenerationError(f"{REST_FILE}: create_app registers no routes")
    return sorted(found, key=lambda e: e.lineno)


def mode_event_patterns() -> dict[str, list[str]]:
    """The ``MODE_EVENT_PATTERNS`` dict literal from notification_service.py."""
    value = _module_value(_parse(NOTIFICATION_FILE), "MODE_EVENT_PATTERNS", NOTIFICATION_FILE)
    if not isinstance(value, ast.Dict):
        raise GenerationError(f"{NOTIFICATION_FILE}: MODE_EVENT_PATTERNS is not a dict literal")
    patterns: dict[str, list[str]] = {}
    for key, val in zip(value.keys, value.values, strict=True):
        if key is None:
            raise GenerationError(f"{NOTIFICATION_FILE}: MODE_EVENT_PATTERNS uses unpacking")
        mode = str(_literal(key))
        raw = _literal(val)
        if not isinstance(raw, list) or not all(isinstance(p, str) for p in raw):
            raise GenerationError(
                f"{NOTIFICATION_FILE}: MODE_EVENT_PATTERNS[{mode!r}] is not a string tuple"
            )
        patterns[mode] = raw
    if not patterns:
        raise GenerationError(f"{NOTIFICATION_FILE}: MODE_EVENT_PATTERNS is empty")
    return patterns


@dataclass(frozen=True)
class FieldInfo:
    name: str
    type: str
    default: str
    desc: str


def _default_repr(node: ast.expr) -> str:
    value = _literal(node)
    if value is None:
        return "None"
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


def config_model_fields(class_name: str) -> list[FieldInfo]:
    """Annotated fields of one pydantic BaseModel in base/config.py."""
    tree = _parse(CONFIG_FILE)
    cls = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name),
        None,
    )
    if cls is None:
        raise GenerationError(f"{CONFIG_FILE}: class {class_name} not found")
    fields: list[FieldInfo] = []
    for stmt in cls.body:
        if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
            continue
        field_name = stmt.target.id
        type_str = ast.unparse(stmt.annotation)
        default = "—（必填）"
        desc = ""
        value = stmt.value
        if value is None:
            pass
        elif (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "Field"
        ):
            if value.args:
                raise GenerationError(
                    f"{CONFIG_FILE}:{stmt.lineno} {class_name}.{field_name} uses "
                    "positional Field() args — teach the generator"
                )
            kwargs = {kw.arg: kw.value for kw in value.keywords if kw.arg}
            if "default" in kwargs:
                default = _default_repr(kwargs["default"])
            elif "default_factory" in kwargs:
                factory = kwargs["default_factory"]
                if isinstance(factory, ast.Lambda):
                    default = ast.unparse(factory.body)
                else:
                    default = f"{ast.unparse(factory)}()"
            if "description" in kwargs:
                desc = str(_literal(kwargs["description"]))
            if "exclude" in kwargs and _literal(kwargs["exclude"]) is True:
                note = "运行时字段，不进 YAML"
                desc = f"{desc}；{note}" if desc else note
        else:
            default = _default_repr(value)
        fields.append(FieldInfo(name=field_name, type=type_str, default=default, desc=desc))
    if not fields:
        raise GenerationError(f"{CONFIG_FILE}: {class_name} has no annotated fields")
    return fields


@dataclass(frozen=True)
class EnvVarInfo:
    name: str
    default: str
    comment: str
    lineno: int


def env_vars() -> list[EnvVarInfo]:
    """Every ``os.getenv("NAME", default)`` call in base/settings.py."""
    source = SETTINGS_FILE.read_text(encoding="utf-8")
    lines = source.splitlines()
    tree = ast.parse(source, filename=str(SETTINGS_FILE))
    found: list[EnvVarInfo] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "getenv"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
        ):
            continue
        if not node.args:
            raise GenerationError(f"{SETTINGS_FILE}:{node.lineno} os.getenv() without args")
        name = str(_literal(node.args[0]))
        if len(node.args) > 1:
            raw_default = _literal(node.args[1])
            default = '""' if raw_default == "" else str(raw_default)
        else:
            default = "（未设置）"
        # Trailing comment on the call's line wins; otherwise the contiguous
        # comment block immediately above the statement.
        comment = ""
        if node.end_col_offset is not None:
            trailing = lines[node.lineno - 1][node.end_col_offset :].strip()
            if trailing.startswith("#"):
                comment = trailing[1:].strip()
        if not comment:
            block: list[str] = []
            i = node.lineno - 2
            while i >= 0 and lines[i].strip().startswith("#"):
                block.append(lines[i].strip()[1:].strip())
                i -= 1
            comment = " ".join(reversed(block))
        found.append(EnvVarInfo(name=name, default=default, comment=comment, lineno=node.lineno))
    if not found:
        raise GenerationError(f"{SETTINGS_FILE}: no os.getenv calls found")
    return sorted(found, key=lambda v: v.lineno)


# ==================== Section renderers ====================


def render_tool_registry() -> str:
    """Registration table for docs/reference/tools-reference.md."""
    rows: list[str] = []
    for domain, definitions in operation_domains().items():
        module = "runs" if domain == "run" else domain
        for operation in definitions:
            rows.append(
                f"| {len(rows) + 1} | `{operation.name}` | "
                f"[operations/{module}.py](../../src/tlon/operations/{module}.py) "
                f"的 `OperationTool` 投影 | {domain} |"
            )
    count = len(rows)
    domains = operation_domains()
    header = (
        f"满配成员注册 {count} 个工具（由共享 Operation 按域列出；"
        f"L0/L1/L2 均注册 {len(domains['filesystem'])} 个文件操作，"
        "自己的 workspace 无需装置；L2 再增加 "
        f"{len(domains['shell'])} 个 Shell 操作；时间不依赖装置）：{GENERATED_NOTE}\n\n"
        "| # | 工具名 | 实现 | 域 |\n"
        "|---|---|---|---|"
    )
    return header + "\n" + "\n".join(rows)


def operation_domains() -> dict[str, list[Operation]]:
    """Definitions shared with production adapters, with no copied operation inventory."""
    return {
        "context": [member_context_operation()],
        "mail": list(mail_operation_definitions()),
        "filesystem": list(filesystem_operation_definitions()),
        "shell": list(shell_operation_definitions()),
        "time": list(time_operation_definitions()),
        "run": [*run_read_operation_definitions(), *run_operation_definitions()],
        "tasks": list(task_operation_definitions()),
        "identity": list(identity_operation_definitions()),
        "observability": list(observability_operation_definitions()),
        "state": list(state_operation_definitions()),
        "lifecycle": list(lifecycle_operation_definitions()),
        "registry": list(make_registry_query_operations()),
        "evolution": list(evolution_operation_definitions()),
        "model": list(model_operation_definitions()),
    }


def _schema_type(schema: Any) -> str:
    if type(schema) is bool:
        return "JSON" if schema else "不可满足"
    kind = schema["type"]
    if isinstance(kind, list):
        return " / ".join(kind)
    if kind == "array":
        return f"array[{_schema_type(schema['items'])}]"
    return str(kind)


def render_operations(domain: str) -> str:
    """Schemas come from the same definitions used by both runtime adapters."""
    blocks = [f"Operation 单一契约；输入与输出字段保留 snake_case。{GENERATED_NOTE}"]
    for operation in operation_domains()[domain]:
        blocks.extend([f"### {operation.name}", operation.description])
        for label, schema in (("输入", operation.input_schema), ("输出", operation.output_schema)):
            blocks.append(f"{label}：")
            if type(schema) is bool:
                blocks.append("任意严格 JSON 值。" if schema else "拒绝所有值。")
                continue
            if schema.get("type") != "object":
                blocks.append(
                    "```json\n" + json.dumps(schema, ensure_ascii=False, indent=2) + "\n```"
                )
                continue
            rows = ["| 字段 | 类型 | 必填 | 说明 |", "|---|---|---|---|"]
            required = schema.get("required", [])
            for name, field in schema["properties"].items():
                kind = _schema_type(field)
                constraints = [
                    f"{key}={value}"
                    for key, value in (field.items() if isinstance(field, dict) else [])
                    if key
                    not in {
                        "type",
                        "items",
                        "properties",
                        "required",
                        "additionalProperties",
                        "description",
                    }
                ]
                description = field.get("description", "") if isinstance(field, dict) else ""
                details = "; ".join(([description] if description else []) + constraints) or "—"
                rows.append(
                    f"| `{name}` | {_cell(str(kind))} | {'是' if name in required else '否'} | "
                    f"{_cell(details)} |"
                )
            blocks.append("\n".join(rows))
    return "\n\n".join(blocks)


def render_event_types() -> str:
    """Flat EventType table for docs/reference/api-reference.md."""
    rows = [f"| `{e.value}` | `{e.member}` | {e.comment} |" for e in event_types()]
    header = (
        f"枚举全集（{len(rows)} 个，生成自源码，注释即源码行内注释）：{GENERATED_NOTE}\n\n"
        "| event_type | 枚举成员 | 说明 |\n"
        "|---|---|---|"
    )
    return header + "\n" + "\n".join(rows)


def _cell(text: str) -> str:
    """Escape a value for a markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ")


def render_dsl_special_forms() -> str:
    """SPECIAL_FORMS whitelist for docs/reference/tlosure-reference.md §2.1."""
    forms = _frozenset_strings(EVAL_FILE, "SPECIAL_FORMS")
    names = "、".join(f"`{f}`" for f in forms)
    return f"封闭白名单（{len(forms)} 个）：{names}。{GENERATED_NOTE}"


def render_dsl_on_events() -> str:
    """Legal ``:on`` keywords for docs/reference/dsl-reference.md §2.1."""
    events = event_types()
    rows = [
        "| `:boot` | — | 启动入口：映射 `None`，订阅就绪后运行一次，`msg` / `event` 均为 `nil` |"
    ]
    for e in events:
        keyword = ":" + e.member.lower().replace("_", "-")
        rows.append(f"| `{keyword}` | `{e.value}` | {_cell(e.comment)} |")
    header = (
        f"合法值共 {len(rows)} 个（`:boot` + 全部 `EventType` 成员，"
        f"转换逻辑 `member_spec._handler_event` / `MemberHandler.event_name`）：{GENERATED_NOTE}\n\n"
        "| `:on` 关键字 | 事件值 | 说明 |\n"
        "|---|---|---|"
    )
    return header + "\n" + "\n".join(rows)


def render_builtin_table(registry: str, path: Path) -> str:
    """One builtin registry table for the DSL reference docs (the general
    vocabularies land in tlosure-reference.md §3, the host registries in
    dsl-reference.md §4)."""
    entries = builtin_registry(registry, path)
    rows = []
    for e in entries:
        pure = "是" if e.pure else "否"
        rows.append(f"| `{e.name}` | {pure} | {_cell(e.doc)} |")
    header = (
        f"`{registry}`（{len(rows)} 个，按源码定义顺序）：{GENERATED_NOTE}\n\n"
        "| 名称 | pure | 说明 |\n"
        "|---|---|---|"
    )
    return header + "\n" + "\n".join(rows)


def render_rest_endpoints() -> str:
    """Route table for docs/reference/api-reference.md 端点总表."""
    endpoints = rest_endpoints()
    rows = [
        f"| {e.method} | `{e.path}` | `{_cell(e.handler)}` | {_cell(e.condition)} |"
        for e in endpoints
    ]
    header = (
        f"`create_app` 共注册 {len(rows)} 条路由（按源码行号排序；"
        f"「注册条件」是条件挂载的守卫，`无条件` = 总是注册）：{GENERATED_NOTE}\n\n"
        "| 方法 | 路径 | handler | 注册条件 |\n"
        "|---|---|---|---|"
    )
    return header + "\n" + "\n".join(rows)


def render_mode_patterns() -> str:
    """MODE_EVENT_PATTERNS table for docs/reference/api-reference.md."""
    patterns = mode_event_patterns()
    rows = []
    for mode, pats in patterns.items():
        shown = "、".join(f"`{p}`" for p in pats)
        rows.append(f"| `{mode}` | {_cell(shown)} |")
    header = (
        f"生成自 `MODE_EVENT_PATTERNS`（`.*` 后缀为前缀匹配，其余精确匹配；"
        f"表外的 mode —— 即 `event` —— 不加限制）：{GENERATED_NOTE}\n\n"
        "| mode | 事件模式 |\n"
        "|---|---|"
    )
    return header + "\n" + "\n".join(rows)


def render_model_table(class_name: str) -> str:
    """One pydantic model's field table for docs/reference/config-reference.md."""
    fields = config_model_fields(class_name)
    rows = [
        f"| `{f.name}` | `{_cell(f.type)}` | {_cell(f.default)} | {_cell(f.desc)} |" for f in fields
    ]
    header = (
        f"`{class_name}` 字段（{len(rows)} 个，按源码声明顺序）：{GENERATED_NOTE}\n\n"
        "| 字段 | 类型 | 默认 | 说明 |\n"
        "|---|---|---|---|"
    )
    return header + "\n" + "\n".join(rows)


def render_env_vars() -> str:
    """os.getenv inventory for docs/reference/config-reference.md §4."""
    variables = env_vars()
    rows = [f"| `{v.name}` | {_cell(v.default)} | {_cell(v.comment)} |" for v in variables]
    header = (
        f"`Settings` 读取的环境变量（{len(rows)} 个，按源码顺序）：{GENERATED_NOTE}\n\n"
        "| 变量 | 默认 | 说明 |\n"
        "|---|---|---|"
    )
    return header + "\n" + "\n".join(rows)


# ==================== Bridge API sections (ADR 0020) ====================


#: Purpose of each language-level evaluation-budget constant (generator
#: configuration — the source defines the value, the doc defines why).
_FUEL_CONSTANT_PURPOSES = {
    "DEFAULT_FUEL": "普通程序求值的默认预算",
    "MAX_FUEL": "程序 `:fuel` 自声明的硬顶（超过即加载拒绝）",
    "DEFAULT_MAX_DEPTH": "form 嵌套深度上限",
}


def render_dsl_fuel_defaults() -> str:
    """Fuel/depth constants from tlosure/eval.py for dsl-reference.md §3."""
    tree = _parse(EVAL_FILE)
    rows: list[str] = []
    for name, purpose in _FUEL_CONSTANT_PURPOSES.items():
        value = _literal(_module_value(tree, name, EVAL_FILE))
        if isinstance(value, bool) or not isinstance(value, int):
            raise GenerationError(f"{EVAL_FILE}: {name} is not an int literal")
        rows.append(f"| `{name}` | {value} | {purpose} |")
    header = (
        f"语言级求值预算常量（`tlosure/eval.py`，{len(rows)} 个；"
        f"上文的默认值/上限数字以此表为准）：{GENERATED_NOTE}\n\n"
        "| 常量 | 值 | 用途 |\n"
        "|---|---|---|"
    )
    return header + "\n" + "\n".join(rows)


def render_bridge_query_ports() -> str:
    """Registry queries share ordinary operation contracts and receipts."""
    return (
        "注册表读取使用普通只读 Operation：`member-sleeping`、`member-running`、"
        '`member-lazy` 接受 `{:agent_id "…"}`，`member-roster` 接受 `{}`。'
        "调用结果经统一 invocation 回执采样与重放；不再注入独立宿主谓词或写入 effects 表。"
        + GENERATED_NOTE
    )


def _render_sections() -> dict[tuple[Path, str], str]:
    return {
        (TOOLS_REF, "tool-registry"): render_tool_registry(),
        **{
            (TOOLS_REF, f"{domain}-operations"): render_operations(domain)
            for domain in operation_domains()
        },
        (API_REF, "event-types"): render_event_types(),
        (TLOSURE_REF, "dsl-special-forms"): render_dsl_special_forms(),
        (TLOSURE_REF, "dsl-builtins-core"): render_builtin_table("CORE_BUILTINS", BUILTINS_FILE),
        (TLOSURE_REF, "dsl-builtins-arithmetic"): render_builtin_table(
            "ARITHMETIC_BUILTINS", BUILTINS_FILE
        ),
        (DSL_REF, "dsl-on-events"): render_dsl_on_events(),
        (DSL_REF, "dsl-builtins-pure"): render_builtin_table("SCRIPT_BUILTINS", VOCABULARY_FILE),
        (DSL_REF, "dsl-fuel-defaults"): render_dsl_fuel_defaults(),
        (DSL_REF, "bridge-query-ports"): render_bridge_query_ports(),
        (API_REF, "rest-endpoints"): render_rest_endpoints(),
        (API_REF, "mode-patterns"): render_mode_patterns(),
        (CONFIG_REF, "agent-yaml-fields-agent"): render_model_table("AgentConfig"),
        (CONFIG_REF, "agent-yaml-fields-llm"): render_model_table("LLMConfig"),
        (CONFIG_REF, "agent-yaml-fields-docker"): render_model_table("DockerRuntimeConfig"),
        (CONFIG_REF, "agent-yaml-fields-tool"): render_model_table("ToolConfig"),
        (CONFIG_REF, "env-vars"): render_env_vars(),
    }


def _apply(text: str, marker: str, content: str, path: Path) -> str:
    begin = f"<!-- BEGIN GENERATED: {marker} -->"
    end = f"<!-- END GENERATED: {marker} -->"
    if begin not in text or end not in text:
        raise GenerationError(f"{path}: markers for '{marker}' not found")
    head, rest = text.split(begin, 1)
    _, tail = rest.split(end, 1)
    return f"{head}{begin}\n{content}\n{end}{tail}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="freshness gate: exit 1 without writing when a generated block is stale",
    )
    args = parser.parse_args()

    sections = _render_sections()
    stale: list[str] = []
    for (path, marker), content in sections.items():
        original = path.read_text(encoding="utf-8")
        updated = _apply(original, marker, content, path)
        if updated == original:
            continue
        if args.check:
            stale.append(f"{path.relative_to(REPO_ROOT)} (section: {marker})")
        else:
            path.write_text(updated, encoding="utf-8")
            print(f"updated {path.relative_to(REPO_ROOT)} (section: {marker})")

    if args.check:
        if stale:
            print("❌ generated docs are stale:", file=sys.stderr)
            for entry in stale:
                print(f"  - {entry}", file=sys.stderr)
            print(
                "run `uv run python scripts/gen_reference_docs.py` and commit the result",
                file=sys.stderr,
            )
            return 1
        print("✅ generated docs are fresh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
