"""Ordinary member vocabulary and materialized events."""

import re
from typing import Any, TypedDict

from tlon.operations.definitions import host_operation_definitions
from tlon.organization.mail.mail import Mail, address_for_member
from tlosure.builtins import (
    ARITHMETIC_BUILTINS,
    CORE_BUILTINS,
    Builtin,
    EvalError,
)
from tlosure.edn import Keyword

# ── Environment contracts (the data each context may see) ──
#
# Every evaluation context binds a fixed set of symbols with a fixed
# shape; these TypedDicts are those shapes and the builders below are the
# single construction point (the engine call sites delegate here).
# Load-time validation and runtime evaluation use these same explicit scopes.


class SelfEnv(TypedDict):
    """The ``self`` binding: the evaluating member's identity.

    ``address`` is pre-derived at construction so DSL builtins read data,
    never call back into tlon (ADR 0018).
    """

    id: str
    name: str
    address: str


def entry_env(payload: Any, *, member_id: str, name: str) -> dict[str, Any]:
    """Materialize event data before passing it to an ordinary entry."""
    mail = payload.get("mail") if isinstance(payload, dict) else None
    return {
        "msg": _materialize(mail),
        "event": _materialize(payload),
        "self": self_env(member_id, name),
    }


def self_env(member_id: str, name: str) -> SelfEnv:
    return {"id": member_id, "name": name, "address": address_for_member(member_id)}


def _materialize(value: Any) -> Any:
    """Copy event data, projecting mail into its public serialized record."""
    if isinstance(value, Mail):
        return _materialize(value.to_dict())
    if isinstance(value, dict):
        return {key: _materialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_materialize(item) for item in value]
    return value


def _builtin_from(env: dict[str, Any], msg: Any, address: Any) -> bool:
    """Compare an explicit mail record's sender with an explicit address."""
    return isinstance(msg, dict) and isinstance(address, str) and msg.get("from_address") == address


def _builtin_content_matches(env: dict[str, Any], target: Any, pattern: Any) -> bool:
    """Regex-search a mail's content or a plain string (e.g. ``result``).

    ``target=None`` (a non-mail event's ``msg``) safely yields False;
    any other non-mail non-string target is a spec bug and raises.
    """
    if target is None:
        return False
    if isinstance(target, dict):
        content = target.get("content")
        if not isinstance(content, str):
            raise EvalError("content-matches? mail record content must be a string")
    elif type(target) is str:
        content = target
    else:
        raise EvalError(f"content-matches? expects a mail or string, got {type(target).__name__}")
    if not isinstance(pattern, str):
        raise EvalError(f"content-matches? pattern must be a string, got {type(pattern).__name__}")
    try:
        return re.search(pattern, content) is not None
    except re.error as e:
        raise EvalError(f"content-matches? invalid regex {pattern!r}: {e}") from e


# ── Explicit pure member functions ──


def _mail_field(env: dict[str, Any], mail: Any, field: Any) -> Any:
    fields = {
        "id": "id",
        "from": "from_address",
        "to": "to",
        "cc": "cc",
        "subject": "subject",
        "content": "content",
        "timestamp": "timestamp",
        "thread-id": "thread_id",
        "in-reply-to": "in_reply_to",
        "forward-of": "forward_of",
        "endorsing": "endorsing",
    }
    if not isinstance(field, Keyword) or str(field) not in fields:
        raise EvalError(f"mail-field: unknown field {field!r}")
    if mail is None:
        return None
    if not isinstance(mail, dict):
        raise EvalError("mail-field expects a materialized mail record or nil")
    return mail.get(fields[str(field)])


SCRIPT_BUILTINS: dict[str, Builtin] = {
    **CORE_BUILTINS,
    **ARITHMETIC_BUILTINS,
    "mail-field": Builtin(_mail_field, pure=True),
    "from?": Builtin(_builtin_from, pure=True),
    "content-matches?": Builtin(_builtin_content_matches, pure=True),
}


# ── Static operation vocabulary ──


def _unavailable_operation(_env: dict[str, Any], *args: Any) -> Any:
    raise EvalError("Operation unavailable for this member")


def make_member_builtins() -> dict[str, Builtin]:
    """Build language helpers and ordinary operation placeholders."""
    builtins: dict[str, Builtin] = {
        **{
            operation.name: Builtin(fn=_unavailable_operation, pure=False)
            for operation in host_operation_definitions()
        },
        **SCRIPT_BUILTINS,
        "tool-execute": Builtin(_unavailable_operation, pure=False),
    }
    return builtins
