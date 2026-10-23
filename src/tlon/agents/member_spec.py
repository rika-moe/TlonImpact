"""Version 2 member entry declarations; workflows live in ordinary modules."""

from dataclasses import dataclass, field
from typing import Any

from tlon.base.events.event import EventType
from tlosure.builtins import Builtin
from tlosure.edn import Form, Keyword, Symbol, dumps
from tlosure.eval import DEFAULT_FUEL, MAX_FUEL

MEMBER_SPEC_VERSION = 2
EXECUTION_LEVELS = frozenset({"L0", "L1", "L2"})


class MemberSpecValidationError(ValueError):
    """A member declaration failed structural validation."""


@dataclass(frozen=True)
class ReflectSpec:
    """Periodic background memory audit settings."""

    enabled: bool
    interval_minutes: int


@dataclass(frozen=True)
class MemberRule:
    """Event subscription projected from one ordinary module handler."""

    name: str
    on: EventType | None


@dataclass(frozen=True)
class MemberSpec:
    """Event handlers and reviewed execution level for a source package."""

    version: int
    rules: tuple[MemberRule, ...]
    source_hash: str | None = None
    execution_level: str | None = None
    handlers: tuple[tuple[str, str], ...] = ()
    fuel: int = DEFAULT_FUEL
    module_sources: dict[str, str] = field(default_factory=dict, compare=False, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 2,
            "handlers": {name: Symbol(entry) for name, entry in self.handlers},
            "fuel": self.fuel,
            "execution": {"level": self.execution_level} if self.execution_level else None,
            "source_hash": self.source_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemberSpec":
        if type(data.get("version")) is not int or data["version"] != MEMBER_SPEC_VERSION:
            raise MemberSpecValidationError("only :version 2 module programs are supported")
        allowed = {"version", "handlers", "fuel", "execution", "source_hash"}
        if unknown := set(data) - allowed:
            raise MemberSpecValidationError(f"version 2 unknown fields: {sorted(unknown)}")
        handlers = data.get("handlers")
        if type(handlers) is not dict or not handlers:
            raise MemberSpecValidationError(":handlers must be a non-empty map")
        parsed: list[tuple[str, str]] = []
        rules: list[MemberRule] = []
        for event, entry in handlers.items():
            if not isinstance(event, str) or type(entry) is not Symbol:
                raise MemberSpecValidationError(
                    ":handlers requires event keys and qualified symbols"
                )
            name = str(event).replace("_", "-")
            on = _on_from(name, name)
            if "/" not in str(entry):
                raise MemberSpecValidationError("handler must be a qualified module function")
            if any(existing == name for existing, _ in parsed):
                raise MemberSpecValidationError("duplicate handler event")
            parsed.append((name, str(entry)))
            rules.append(MemberRule(name=f"handler:{name}", on=on))
        fuel = data.get("fuel", DEFAULT_FUEL)
        if type(fuel) is not int or not 0 < fuel <= MAX_FUEL:
            raise MemberSpecValidationError(f":fuel must be in 1..{MAX_FUEL}")
        source_hash = data.get("source_hash")
        if source_hash is not None and type(source_hash) is not str:
            raise MemberSpecValidationError(":source-hash must be a string")
        execution = data.get("execution")
        level = None
        if execution is not None:
            if type(execution) is not dict or set(execution) != {"level"}:
                raise MemberSpecValidationError(":execution must contain only :level")
            level = execution["level"]
            if not isinstance(level, str) or str(level) not in EXECUTION_LEVELS:
                raise MemberSpecValidationError(":execution/:level must be one of :L0/:L1/:L2")
            level = str(level)
        return cls(2, tuple(rules), source_hash, level, tuple(parsed), fuel)

    @classmethod
    def from_edn(cls, data: Any) -> "MemberSpec":
        if not isinstance(data, dict):
            raise MemberSpecValidationError("spec must contain a top-level map")
        return cls.from_dict(_normalize_data(data))

    def to_edn(self) -> str:
        return dumps(self.to_edn_data())

    def to_edn_data(self) -> dict[Keyword, Any]:
        data: dict[Keyword, Any] = {
            Keyword("version"): 2,
            Keyword("handlers"): {Keyword(name): Symbol(entry) for name, entry in self.handlers},
            Keyword("fuel"): self.fuel,
        }
        if self.execution_level is not None:
            data[Keyword("execution")] = {Keyword("level"): Keyword(self.execution_level)}
        return data


def validate_member_spec(
    member: MemberSpec, *, builtins: dict[str, Builtin] | None = None
) -> MemberSpec:
    """Validate declarations; complete module admission is program_package's job."""
    MemberSpec.from_dict(member.to_dict())
    return member


def _normalize_data(value: Any) -> Any:
    """Recursively normalize EDN data: map keys become snake_case strings.

    Forms (``(...)``) are expressions, not data — they pass through
    untouched so their keywords/symbols keep their literal names.
    """
    if isinstance(value, Form):
        return value
    if isinstance(value, dict):
        return {_normalize_key(key): _normalize_data(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_data(item) for item in value]
    return value


def _normalize_key(key: Any) -> str:
    """Map an EDN key (``:max-iterations``) to a snake_case field name."""
    if isinstance(key, (Keyword, str)):
        return str(key).replace("-", "_")
    raise MemberSpecValidationError(f"spec keys must be keywords, got {_type_name(key)}: {key!r}")


def _on_from(value: Any, rule_name: str) -> EventType | None:
    """Map an ``:on`` keyword to an EventType; ``:boot``/nil becomes None."""
    if value is None or (isinstance(value, str) and str(value) == "boot"):
        return None
    if not isinstance(value, str):
        raise MemberSpecValidationError(
            f"rule {rule_name!r}: :on must be an event keyword, got {_type_name(value)}: {value!r}"
        )
    event_name = str(value).replace("-", "_").upper()
    try:
        return EventType[event_name]
    except KeyError:
        raise MemberSpecValidationError(
            f"rule {rule_name!r}: unknown event :{value} in :on"
        ) from None


def _on_to_str(on: EventType | None) -> str | None:
    """Serialize an EventType to its EDN keyword name; None = :boot."""
    if on is None:
        return None
    return on.name.lower().replace("_", "-")


def _type_name(value: Any) -> str:
    return type(value).__name__
