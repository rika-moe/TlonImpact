"""Capability artifact schema and the load-time validation chain.

A capability is data, not host code (docs/design/capabilities.md, issue
#45): one tlosure function artifact per word —

.. code-block:: clojure

    {:version 1
     :name "thread-digest"
     :description "把邮件线程蒸馏成三段式摘要文本"
     :args [{:name "mails" :type :list :doc "按时间升序的邮件列表"}]
     :impl (fn [mails] ...)
     :pure true}

The validation registries below are the structural enforcement of the
**env contract "sees args only, zero injection"** (2026-10-05 拍板).
Their composition was verified word by word against the sources:

- :data:`CAPABILITY_PURE_BUILTINS` = ``CORE_BUILTINS`` +
  ``ARITHMETIC_BUILTINS`` (19 words). Every builtin fn in both
  dictionaries was read line by line (``tlosure/builtins.py``): each
  takes ``(env, *args)`` and **never touches ``env``** — they are total
  functions of their positional arguments. This is the same composition
  and the same environment contract as ``POLICY_BUILTINS``
  (``agents/vocabulary.py``, ADR 0022): hooks receive materialized data
  and no symbols.
- **Excluded**: the trigger / loop / stage-cond predicate registries
  (``TRIGGER_BUILTINS`` / ``LOOP_BUILTINS`` / ``STAGE_COND_BUILTINS``)
  read ``env`` directly (``self`` / ``state`` / ``scratch`` / ``round``
  sections) — under a zero-injection env they would silently degrade,
  so they are not capability vocabulary. The closure-injected host
  queries (``sleeping?`` / ``running?`` / ``lazy?`` / ``member-roster``)
  read registry state and are likewise excluded. Narrowing beats
  widening: a word that needs context is composed at the call site with
  explicit arguments instead.
- :data:`CAPABILITY_ACTION_BUILTINS` = the six member action words
  (``MEMBER_ACTION_NAMES``). Their fns were verified to not read
  ``env`` either: they validate their arguments and produce
  :class:`ActionRequest` data. ``start-all`` reads the closure-injected
  host (never ``env``); the registry here is validation-only —
  ``validate_form`` checks names, never evaluates.

Structural rules enforced by :func:`CapabilitySpec.from_edn`:

- ``:version`` must be the int ``1``;
- ``:name`` is the word the artifact defines: a string that is both a
  legal tlosure symbol and a safe directory name — first character a
  letter, then letters / digits / ``-`` / ``_``;
- ``:args`` is a vector of ``{:name :type :doc}`` maps; ``:type`` is one
  of ``:string`` / ``:number`` / ``:boolean`` / ``:list`` / ``:map``
  (review material, not a static guarantee — tlosure has no type
  system);
- ``:impl`` must be a ``(fn [params] body...)`` literal — not ``defn``,
  not any other form; variadic parameters (``& rest``) are rejected
  (v1: fixed arity only), and the declared ``:args`` count must equal
  the fn's fixed parameter count;
- ``:pure`` must be a bool.

:func:`validate_capability` is the load gate (called by the staging
write path now, and by the trusted-library loader when the vocabulary
bridge lands): namespace collision (special forms / builtins / other
trusted capabilities — shadowing is rejected, never prefixed) and the
purity admission (a ``:pure true`` impl must validate against
:data:`CAPABILITY_PURE_BUILTINS`; a non-pure impl may additionally
compose the action words and yields ActionRequest data).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from tlon.agents.vocabulary import MEMBER_ACTION_NAMES, make_member_builtins
from tlosure.builtins import ARITHMETIC_BUILTINS, CORE_BUILTINS, Builtin, EvalError
from tlosure.edn import EDNParseError, Form, Keyword, Symbol, dumps, loads
from tlosure.eval import SPECIAL_FORMS
from tlosure.validate import _params, validate_form

#: The arg ``:type`` keywords a capability may declare (review material,
#: not a static guarantee — the fn application enforces arity at runtime).
ARG_TYPES = frozenset({"string", "number", "boolean", "list", "map"})


class CapabilityError(ValueError):
    """Capability artifact parse, validation, or staging-flow failure."""


#: Pure capability vocabulary: the env-free general builtins. Every word
#: was verified to never read ``env`` (see the module docstring); a pure
#: capability's ``:impl`` must statically validate against exactly this
#: registry — naming an action word is a load-time rejection.
CAPABILITY_PURE_BUILTINS: dict[str, Builtin] = {**CORE_BUILTINS, **ARITHMETIC_BUILTINS}

#: The six member action words, lifted from the unified member registry
#: (host-less, collector-less: validation only needs the names and the
#: purity markers — ``validate_form`` never calls the fns).
CAPABILITY_ACTION_BUILTINS: dict[str, Builtin] = {
    name: builtin for name, builtin in make_member_builtins().items() if name in MEMBER_ACTION_NAMES
}

#: Non-pure capability vocabulary: pure builtins plus the action words
#: (an impure capability composes them to produce ActionRequest data).
CAPABILITY_FULL_BUILTINS: dict[str, Builtin] = {
    **CAPABILITY_PURE_BUILTINS,
    **CAPABILITY_ACTION_BUILTINS,
}


@dataclass(frozen=True)
class ArgSpec:
    """One declared positional argument (``{:name :type :doc}``)."""

    name: str
    type: str
    doc: str


@dataclass(frozen=True)
class CapabilitySpec:
    """One capability artifact (``capability.tlon``), one word.

    ``impl`` is the parsed ``(fn [params] body...)`` form — the whole
    behavior, statically validated at load and fuel-bounded at
    evaluation, counted into the caller's fuel budget.
    """

    version: int
    name: str
    description: str
    args: tuple[ArgSpec, ...]
    impl: Form
    pure: bool

    @staticmethod
    def from_edn(text: str) -> "CapabilitySpec":
        """Parse and structurally validate one artifact EDN text.

        Raises:
            CapabilityError: EDN parse failure or any structural rule
                violation (see the module docstring)
        """
        try:
            data = loads(text)
        except EDNParseError as e:
            raise CapabilityError(f"capability EDN parse failed: {e}") from e
        if not isinstance(data, dict):
            raise CapabilityError("capability artifact must be a map")

        version = data.get("version")
        if isinstance(version, bool) or version != 1:
            raise CapabilityError(f"capability :version must be 1, got {version!r}")

        name = data.get("name")
        # ``type is str``: Keyword/Symbol are str subclasses but are not
        # the string the schema declares.
        if type(name) is not str:
            raise CapabilityError(f"capability :name must be a string, got {name!r}")
        _validate_word_name(name)

        description = data.get("description")
        if type(description) is not str:
            raise CapabilityError(f"capability :description must be a string, got {description!r}")

        args = _parse_args(data.get("args"))
        impl = _parse_impl(data.get("impl"), arg_count=len(args))

        pure = data.get("pure")
        if not isinstance(pure, bool):
            raise CapabilityError(f"capability :pure must be a bool, got {pure!r}")

        return CapabilitySpec(
            version=1,
            name=name,
            description=description,
            args=args,
            impl=impl,
            pure=pure,
        )

    def to_edn(self) -> str:
        """Serialize back to the artifact EDN text (round-trips ``from_edn``)."""
        data: dict[Any, Any] = {
            Keyword("version"): self.version,
            Keyword("name"): self.name,
            Keyword("description"): self.description,
            Keyword("args"): [
                {
                    Keyword("name"): arg.name,
                    Keyword("type"): Keyword(arg.type),
                    Keyword("doc"): arg.doc,
                }
                for arg in self.args
            ],
            Keyword("impl"): self.impl,
            Keyword("pure"): self.pure,
        }
        return dumps(data) + "\n"


def _validate_word_name(name: str) -> None:
    """The word name must be a legal tlosure symbol and a safe directory name.

    Digits cannot start a symbol (the EDN reader would parse a number),
    so the first character must be a letter; the rest mirrors the
    prototype-name charset (letters / digits / ``-`` / ``_``), a strict
    subset of the EDN symbol charset.
    """
    if (
        not name
        or not name[0].isalpha()
        or not name[1:].replace("-", "").replace("_", "").isalnum()
    ):
        raise CapabilityError(
            f"invalid capability name {name!r}: must start with a letter, "
            "then only letters, digits, '-' and '_'"
        )


def _parse_args(raw: Any) -> tuple[ArgSpec, ...]:
    """Validate the ``:args`` vector of ``{:name :type :doc}`` maps."""
    if isinstance(raw, Form) or not isinstance(raw, list):
        raise CapabilityError("capability :args must be a vector")
    args: list[ArgSpec] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise CapabilityError(f"capability :args[{i}] must be a map, got {item!r}")
        name = item.get("name")
        if type(name) is not str or not name:
            raise CapabilityError(
                f"capability :args[{i}] :name must be a non-empty string, got {name!r}"
            )
        arg_type = item.get("type")
        if not isinstance(arg_type, Keyword) or str(arg_type) not in ARG_TYPES:
            raise CapabilityError(
                f"capability :args[{i}] :type must be one of "
                f"{sorted(':' + t for t in ARG_TYPES)}, got {arg_type!r}"
            )
        doc = item.get("doc")
        if type(doc) is not str:
            raise CapabilityError(f"capability :args[{i}] :doc must be a string, got {doc!r}")
        args.append(ArgSpec(name=name, type=str(arg_type), doc=doc))
    return tuple(args)


def _parse_impl(raw: Any, *, arg_count: int) -> Form:
    """Validate the ``:impl`` fn literal; return the parsed form.

    v1 fixes the shape: exactly ``(fn [params] body...)`` — no ``defn``,
    no bare body form, no variadic parameters — and the declared
    ``:args`` count must equal the fn's fixed parameter count.
    """
    if (
        not isinstance(raw, Form)
        or not raw
        or not isinstance(raw[0], Symbol)
        or str(raw[0]) != "fn"
    ):
        raise CapabilityError(
            f"capability :impl must be a (fn [params] body...) literal, got {dumps(raw)}"
        )
    if len(raw) < 3:
        raise CapabilityError("capability :impl fn needs a parameter vector and a body")
    try:
        params, rest = _params(raw[1], "fn")
    except EvalError as e:
        raise CapabilityError(f"capability :impl parameter vector invalid: {e}") from e
    if rest is not None:
        raise CapabilityError(
            "capability :impl must have fixed arity: variadic '& rest' "
            "parameters are not supported in v1"
        )
    if len(params) != arg_count:
        raise CapabilityError(
            f"capability :args declares {arg_count} argument(s) but :impl's fn "
            f"takes {len(params)} parameter(s)"
        )
    return raw


def validate_capability(spec: CapabilitySpec, *, taken_names: Iterable[str] = ()) -> None:
    """The load gate: namespace rejection + purity admission + impl validation.

    Namespace (shadowing is rejected, never prefixed): the word name
    must not collide with a special form, a builtin of the capability
    registries, or a name in ``taken_names`` (the trusted capability
    library's word names, injected by the caller).

    Purity admission: a ``:pure true`` impl must statically validate
    against :data:`CAPABILITY_PURE_BUILTINS` — naming an action word is
    rejected as unknown vocabulary. A non-pure impl validates against
    :data:`CAPABILITY_FULL_BUILTINS` (pure builtins + the six action
    words, producing ActionRequest data). The validation scope is empty:
    the impl sees its fn parameters and nothing else (the env contract,
    enforced statically).

    Raises:
        CapabilityError: namespace collision or impl validation failure
    """
    if spec.name in SPECIAL_FORMS:
        raise CapabilityError(f"capability name '{spec.name}' collides with a special form")
    if spec.name in CAPABILITY_FULL_BUILTINS:
        raise CapabilityError(f"capability name '{spec.name}' collides with a builtin word")
    if spec.name in set(taken_names):
        raise CapabilityError(f"capability name '{spec.name}' collides with a trusted capability")

    registry = CAPABILITY_PURE_BUILTINS if spec.pure else CAPABILITY_FULL_BUILTINS
    try:
        validate_form(spec.impl, registry, set())
    except EvalError as e:
        which = "pure" if spec.pure else "impure"
        raise CapabilityError(f"capability :impl failed {which} validation: {e}") from e
