"""Lexical call analysis for hosts that require statically named dependencies."""

from typing import Any

from tlosure.builtins import Builtin, EvalError
from tlosure.edn import Form, Symbol
from tlosure.validate import _binding_names, _params, validate_form


def referenced_functions(form: Any, builtins: dict[str, Builtin]) -> frozenset[str]:
    """Validate a closed form and return external function names in every branch.

    Local literal functions and recursion are supported. Calling a parameter,
    computed function value, or other value of unknown provenance is rejected;
    it cannot conceal a host dependency. Quoted data is never executable.
    """
    validate_form(form, builtins, set())
    references: set[str] = set()

    def sequence(forms: list[Any], scope: set[str], functions: set[str]) -> None:
        local, named = set(scope), set(functions)
        for item in forms:
            walk(item, local, named)
            if is_form(item, "defn"):
                local.add(str(item[1]))
                named.add(str(item[1]))

    def is_form(value: Any, head: str) -> bool:
        return isinstance(value, Form) and bool(value) and value[0] == Symbol(head)

    def walk(value: Any, scope: set[str], functions: set[str]) -> None:
        if not isinstance(value, Form):
            items = (
                value.values()
                if isinstance(value, dict)
                else value
                if isinstance(value, list)
                else ()
            )
            for item in items:
                walk(item, scope, functions)
            return
        head = value[0]
        if not isinstance(head, Symbol):
            if not is_form(head, "fn"):
                raise EvalError("dynamic function target is not statically known")
            walk(head, scope, functions)
            for item in value[1:]:
                walk(item, scope, functions)
            return
        name = str(head)
        if name == "quote":
            return
        if name in {"fn", "defn"}:
            index = 1 if name == "fn" else 2
            params, rest = _params(value[index], name)
            local = scope | set(params) | ({rest} if rest else set())
            named = functions - set(params) - ({rest} if rest else set())
            if name == "defn":
                local.add(str(value[1]))
                named.add(str(value[1]))
            sequence(value[index + 1 :], local, named)
            return
        if name in {"let", "while"}:
            local, named = set(scope), set(functions)
            for binding, init in zip(_binding_names(value[1], name), value[1][1::2], strict=True):
                walk(init, local, named)
                local.add(binding)
                named.discard(binding)
                if is_form(init, "fn"):
                    named.add(binding)
            sequence(value[2:], local, named)
            return
        if name in {"map", "filter"}:
            binding = str(value[1])
            walk(value[2], scope | {binding}, functions - {binding})
            walk(value[3], scope, functions)
            return
        if name == "reduce":
            names = {str(value[1]), str(value[2])}
            walk(value[3], scope | names, functions - names)
            for item in value[4:]:
                walk(item, scope, functions)
            return
        if name in scope:
            if name not in functions:
                raise EvalError(f"dynamic function target is not statically known: {name}")
        elif name in builtins:
            references.add(name)
        for item in value[1:]:
            walk(item, scope, functions)

    walk(form, set(), set())
    return frozenset(references)
