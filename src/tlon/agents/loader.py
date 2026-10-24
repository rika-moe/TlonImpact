"""Read current member source and admit it separately from frozen restoration.

The validated loader is the new-behavior gate. Recovery-only activation can read
source structurally while a durable root supplies its own rule and dependencies.
"""

import hashlib
import sqlite3
from dataclasses import replace

from tlon.agents.member_spec import MemberSpec
from tlon.agents.program_package import read_module_sources, validate_program_sources
from tlon.agents.recovery import RecoveryBlockedError
from tlon.base.paths import TlonPaths
from tlosure.builtins import Builtin
from tlosure.edn import loads


def has_frozen_work(agent_id: str, tlon_paths: TlonPaths) -> bool:
    """Inspect unfinished roots without authorizing their execution."""
    diagnostic = legacy_work_error(agent_id, tlon_paths)
    if diagnostic:
        raise RecoveryBlockedError(diagnostic)
    database = tlon_paths.agent_program_database_file(agent_id)
    if not database.exists():
        return False
    try:
        with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as connection:
            if not connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='entry_programs'"
            ).fetchone():
                return False
            return bool(
                connection.execute(
                    "SELECT 1 FROM runs r LEFT JOIN entry_programs e USING(run_id) "
                    "WHERE e.run_id IS NOT NULL AND ((r.completed=0 AND r.error IS NULL) "
                    "OR (e.scheduling_edn IS NOT NULL AND e.scheduling_applied=0)) LIMIT 1"
                ).fetchone()
            )
    except (sqlite3.Error, OSError) as error:
        raise RecoveryBlockedError(
            f"Frozen program scan blocked for {database}: {error}"
        ) from error


def legacy_work_error(agent_id: str, tlon_paths: TlonPaths) -> str | None:
    """Inspect obsolete pending work read-only; preserve evidence and refuse execution.

    An unreadable or unrecognized persisted database blocks admission too. No
    schema initialization or progress writes are allowed during this scan.
    """
    problems: list[str] = []
    database = tlon_paths.agent_program_database_file(agent_id)
    if database.exists():
        try:
            with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if "runs" not in tables:
                    raise ValueError("unrecognized program database")
                query = "SELECT run_id FROM runs WHERE completed=0 AND error IS NULL"
                if "entry_programs" in tables:
                    query += " AND run_id NOT IN (SELECT run_id FROM entry_programs)"
                unfinished = [row[0] for row in connection.execute(query + " ORDER BY run_id")]
                if unfinished:
                    problems.append(f"{database}: unfinished legacy rule IDs={unfinished}")
        except (sqlite3.Error, ValueError, OSError) as error:
            problems.append(f"{database}: legacy rule scan failed: {error}")
    if not problems:
        return None
    return "Legacy recovery blocked; stage execution has been removed. " + "; ".join(problems)


def read_member_spec(agent_id: str, *, tlon_paths: TlonPaths) -> MemberSpec:
    """Read current source structurally, retaining provenance for diagnostics.

    This is not admission to new work; restoration can read invalid current
    behavior while independently executing an older frozen program.
    """
    text = tlon_paths.agent_behavior_file(agent_id).read_text(encoding="utf-8")
    member = MemberSpec.from_edn(loads(text))
    source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    modules = read_module_sources(tlon_paths.agent_behavior_file(agent_id).parent / "modules")
    return replace(member, source_hash=source_hash, module_sources=modules)


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

    ``builtins`` is the registry validated against — host words by default.
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
    diagnostic = legacy_work_error(agent_id, tlon_paths)
    if diagnostic:
        raise RecoveryBlockedError(diagnostic)
    member = read_member_spec(agent_id, tlon_paths=tlon_paths)
    admitted = validate_program_sources(member.to_edn(), member.module_sources, builtins)
    return replace(admitted, source_hash=member.source_hash)
