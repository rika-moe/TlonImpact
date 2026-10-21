"""Explicit, effect-free module linking for tlosure programs.

Sources are supplied by the host; loading never performs file I/O or invokes
host builtins. Linked closures run in the caller's ordinary evaluator budget.
"""

import re
from dataclasses import dataclass
from typing import Any

from tlosure.builtins import Builtin, Closure, EvalError
from tlosure.edn import EDNParseError, Form, Keyword, Symbol, loads_all
from tlosure.eval import SPECIAL_FORMS
from tlosure.validate import _params, validate_form

_NAME = re.compile(r"[a-zA-Z_][a-zA-Z0-9_-]*\Z")
_BINDING = re.compile(r"[a-zA-Z_][a-zA-Z0-9_!?*-]*\Z")


class ModuleError(EvalError):
    """Invalid source package, import, export or entry point."""


@dataclass(frozen=True)
class _Module:
    namespace: str
    imports: dict[str, str]
    exports: list[str]
    definitions: dict[str, Form]


def _symbol(value: Any, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, Symbol) or not pattern.fullmatch(value):
        raise ModuleError(f"invalid {label}: {value!r}")
    return str(value)


def _literal(value: Any) -> bool:
    if isinstance(value, (Form, Symbol)):
        return False
    if isinstance(value, list):
        return all(_literal(item) for item in value)
    if isinstance(value, dict):
        return all(_literal(k) and _literal(v) for k, v in value.items())
    return value is None or isinstance(value, (str, bool, int, float))


def _parse(path: str, source: str, builtins: dict[str, Builtin]) -> _Module:
    if not path.endswith(".tlon"):
        raise ModuleError(f"module source must end in .tlon: {path}")
    parts = path[:-5].split("/")
    if not parts or any(not _NAME.fullmatch(part) for part in parts):
        raise ModuleError(f"invalid module source path: {path}")
    namespace = ".".join(parts)
    forms = loads_all(source)
    if not forms or not isinstance(forms[0], Form):
        raise ModuleError(f"module {namespace} requires an ns declaration")
    declaration = forms[0]
    if (
        len(declaration) < 2
        or not isinstance(declaration[0], Symbol)
        or declaration[0] != Symbol("ns")
    ):
        raise ModuleError(f"module {namespace} requires an ns declaration")
    if not isinstance(declaration[1], Symbol) or str(declaration[1]) != namespace:
        raise ModuleError(f"namespace must match source path: {namespace}")
    imports: dict[str, str] = {}
    exports: list[str] = []
    clauses: set[str] = set()
    for clause in declaration[2:]:
        if not isinstance(clause, Form) or not clause or not isinstance(clause[0], Keyword):
            raise ModuleError("ns clauses must be (:require ...) or (:export ...)")
        kind = str(clause[0])
        if kind in clauses or kind not in {"require", "export"}:
            raise ModuleError(f"duplicate or unknown ns clause: {kind}")
        clauses.add(kind)
        if kind == "export":
            exports = [_symbol(item, _BINDING, "export") for item in clause[1:]]
            if len(set(exports)) != len(exports):
                raise ModuleError("duplicate export")
        else:
            for item in clause[1:]:
                if (
                    not isinstance(item, list)
                    or isinstance(item, Form)
                    or len(item) != 3
                    or not isinstance(item[0], Symbol)
                    or not isinstance(item[1], Keyword)
                    or item[1] != Keyword("as")
                ):
                    raise ModuleError("require must be [namespace :as alias]")
                target = str(item[0])
                if any(not _NAME.fullmatch(part) for part in target.split(".")):
                    raise ModuleError(f"invalid imported namespace: {target}")
                alias = _symbol(item[2], _NAME, "import alias")
                if alias in imports:
                    raise ModuleError(f"duplicate import alias: {alias}")
                imports[alias] = target
    definitions: dict[str, Form] = {}
    for form in forms[1:]:
        if (
            not isinstance(form, Form)
            or len(form) < 3
            or not isinstance(form[0], Symbol)
            or str(form[0]) not in {"def", "defn"}
        ):
            raise ModuleError("module top level permits only defn and literal def")
        name = _symbol(form[1], _BINDING, "binding")
        if name in definitions or name in builtins or name in SPECIAL_FORMS:
            raise ModuleError(f"duplicate or reserved binding: {name}")
        if form[0] == Symbol("def") and (len(form) != 3 or not _literal(form[2])):
            raise ModuleError(f"constant {name} must contain only literal data")
        definitions[name] = form
    for name in exports:
        if name not in definitions or definitions[name][0] != Symbol("defn"):
            raise ModuleError(f"export must name a defined function: {name}")
    return _Module(namespace, imports, exports, definitions)


@dataclass(frozen=True)
class ModulePackage:
    """Linked module sources and their explicitly exported closures."""

    sources: dict[str, str]
    exports: dict[str, Closure]

    @classmethod
    def from_sources(cls, sources: dict[str, str], builtins: dict[str, Builtin]) -> "ModulePackage":
        """Validate and link a closed source package without running its code."""
        try:
            return cls._link(dict(sources), builtins)
        except (EDNParseError, EvalError) as exc:
            if isinstance(exc, ModuleError):
                raise
            raise ModuleError(str(exc)) from exc

    @classmethod
    def _link(cls, sources: dict[str, str], builtins: dict[str, Builtin]) -> "ModulePackage":
        modules: dict[str, _Module] = {}
        for path, source in sources.items():
            module = _parse(path, source, builtins)
            if module.namespace in modules:
                raise ModuleError(f"duplicate module: {module.namespace}")
            modules[module.namespace] = module
        linked: dict[str, dict[str, Any]] = {}
        visiting: set[str] = set()

        def link(namespace: str) -> dict[str, Any]:
            if namespace in linked:
                return linked[namespace]
            if namespace in visiting:
                raise ModuleError(f"module dependency cycle: {namespace}")
            if namespace not in modules:
                raise ModuleError(f"unknown module: {namespace}")
            visiting.add(namespace)
            module = modules[namespace]
            env: dict[str, Any] = {}
            for alias, dependency in module.imports.items():
                dependency_env = link(dependency)
                for name in modules[dependency].exports:
                    env[f"{alias}/{name}"] = dependency_env[name]
            scope = set(env) | set(module.definitions)
            for name, form in module.definitions.items():
                if form[0] == Symbol("def"):
                    env[name] = form[2]
                else:
                    validate_form(form, builtins, scope)
                    params, rest = _params(form[2], "defn")
                    names = params + ([rest] if rest is not None else [])
                    if len(set(names)) != len(names):
                        raise ModuleError(f"duplicate function parameter: {name}")
                    env[name] = Closure(params, rest, list(form[3:]), env, name)
            visiting.remove(namespace)
            linked[namespace] = env
            return env

        exports: dict[str, Closure] = {}
        for namespace, module in modules.items():
            env = link(namespace)
            for name in module.exports:
                exports[f"{namespace}/{name}"] = env[name]
        return cls(sources, exports)

    def entry(self, name: str) -> Closure:
        """Resolve an exported entry accepting exactly one event argument."""
        closure = self.exports.get(name)
        if closure is None:
            raise ModuleError(f"unknown or private entry: {name}")
        if len(closure.params) != 1 or closure.rest is not None:
            raise ModuleError(f"entry must accept exactly one parameter: {name}")
        return closure
