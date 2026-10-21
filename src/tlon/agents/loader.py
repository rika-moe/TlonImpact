"""Read current member source and admit it separately from frozen restoration.

The validated loader is the new-behavior gate. Recovery-only activation can read
source structurally while a durable root supplies its own rule and dependencies.
"""

import hashlib
from dataclasses import replace

from tlon.agents.member_spec import MemberSpec, validate_member_spec
from tlon.base.paths import TlonPaths
from tlosure.builtins import Builtin
from tlosure.edn import loads


def has_frozen_work(agent_id: str, tlon_paths: TlonPaths) -> bool:
    """Inspect unfinished roots without authorizing their execution."""
    from tlon.agents.program_store import ProgramStore
    from tlon.agents.session_store import SessionStore

    database = tlon_paths.agent_program_database_file(agent_id)
    if database.exists() and ProgramStore(database).pending():
        return True
    return bool(SessionStore(tlon_paths.agent_sessions_database_file(agent_id)).unfinished_ids())


def read_member_spec(agent_id: str, *, tlon_paths: TlonPaths) -> MemberSpec:
    """Read current source structurally, retaining provenance for diagnostics.

    This is not admission to new work; restoration can read invalid current
    behavior while independently executing an older frozen program.
    """
    text = tlon_paths.agent_behavior_file(agent_id).read_text(encoding="utf-8")
    member = MemberSpec.from_edn(loads(text))
    source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return replace(member, source_hash=source_hash)


def load_member_spec(
    agent_id: str,
    *,
    tlon_paths: TlonPaths,
    builtins: dict[str, Builtin] | None = None,
) -> MemberSpec:
    """Load and validate one member's unified spec — total semantics.

    ``~/.tlon/{agent_id}/config/behavior.tlon`` must exist, parse as EDN,
    and pass the single validator. Any failure raises — the member refuses
    to boot (the registry logs and isolates the failure; the server and
    other members are unaffected).

    ``builtins`` is the registry validated against — host words and approved
    composites by default.
    Host query words are available only when explicitly injected by the
    caller. Adam passes its registry query builtins explicitly.

    The source hash is computed over the raw file bytes for **every**
    member (sha256, first 12 hex chars) — one
    semantics across the unified format.

    Raises:
        FileNotFoundError: no behavior file (or unreadable)
        EDNParseError: file is not valid EDN
        MemberSpecValidationError: the spec fails the single validator
    """
    if builtins is None:
        from tlon.agents.capabilities.bridge import capability_validation_builtins

        builtins = capability_validation_builtins(tlon_paths)
    return validate_member_spec(
        read_member_spec(agent_id, tlon_paths=tlon_paths), builtins=builtins
    )
