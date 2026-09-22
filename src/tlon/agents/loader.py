"""Unified member-spec loader (phase C of the Agent/Script merge).

Phase A built the :class:`~tlon.agents.member_spec.MemberSpec` model; phase B
made it the single spec flowing through the system (with legacy file formats
still parsed behind projections); phase C unifies the file format itself —
every member carries one rules-form ``behavior.tlon`` and this loader is the
only read path.

One function reads a member's behavior file and returns the validated
:class:`MemberSpec` with provenance attached. The path layout is injected
(``tlon_paths``) — there is no module-level default.
"""

import hashlib
from dataclasses import replace

from tlon.agents.member_spec import MemberSpec, validate_member_spec
from tlon.base.paths import TlonPaths
from tlosure.builtins import Builtin
from tlosure.edn import loads


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

    ``builtins`` is the registry validated against — host-less by default
    (matching the deterministic branch: script-instance specs may not use
    the host predicates, those are Adam's vocabulary). Adam passes its host
    builtins explicitly.

    The source hash is computed over the raw file bytes for **every**
    member (sha256, first 12 hex chars), not just cognitive ones — one
    semantics across the unified format.

    Raises:
        FileNotFoundError: no behavior file (or unreadable)
        EDNParseError: file is not valid EDN
        MemberSpecValidationError: the spec fails the single validator
    """
    behavior_file = tlon_paths.agent_behavior_file(agent_id)
    text = behavior_file.read_text(encoding="utf-8")
    member = MemberSpec.from_edn(loads(text))
    member = validate_member_spec(member, builtins=builtins)
    source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return replace(member, source_hash=source_hash)
