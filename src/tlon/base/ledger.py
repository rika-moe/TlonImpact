"""Metabolic ledger — append-only per-member token accounting (ADR 0014).

Write side: ``LLMClient.chat`` (the only LLM exit) appends one record per
successful call carrying usage to ``~/.tlon/{agent_id}/ledger.jsonl``.
Only the base layer writes; agents have no write path. A client built
without an ``agent_id`` (system-level) records nothing.

Record shape (one JSON object per line)::

    {"ts", "event": "llm_call", "model", "prompt_tokens",
     "completion_tokens", "duration_ms", "cpu_ms", "purpose",
     "session_id"}

``duration_ms`` is the wall time of the whole chat call (retries
included); ``cpu_ms`` is the ``time.process_time()`` delta, best-effort.
``purpose`` / ``session_id`` are attribution labels from the call site
(``None`` when unknown).

Read side: derived queries, never stored (the reputation.py precedent) —
``collect_agent_ledger`` summarizes one member (totals + per-purpose
split), ``collect_lineage_ledgers`` aggregates members by bloodline
(``AgentConfig.prototype`` in each member's ``config/agent.yaml``). Both
recompute from the raw files on every call.

Writes are serialized by one asyncio lock per ledger file, shared
process-wide (bounded LRU — same pattern as the session journal and the
mailbox). The reader tolerates a torn tail line: each line is parsed
independently and a bad line is skipped with a warning.
"""

import asyncio
import json
from collections import OrderedDict
from pathlib import Path
from typing import Any

import aiofiles

from tlon.base.config import yaml_load_all
from tlon.base.logger import get_logger
from tlon.base.paths import TlonPaths

logger = get_logger(__name__)

#: Bound on the shared lock table (same magnitude as the journal's).
_LOCKS_CAP = 4096

#: File-keyed locks shared by every LedgerWriter over the same file
#: (bounded LRU; a locked entry is never evicted — see mailbox._lock_for).
_LOCKS: OrderedDict[Path, asyncio.Lock] = OrderedDict()


def _lock_for(path: Path) -> asyncio.Lock:
    """Return the shared lock for a ledger file (bounded LRU)."""
    lock = _LOCKS.get(path)
    if lock is not None:
        _LOCKS.move_to_end(path)
        return lock
    lock = asyncio.Lock()
    _LOCKS[path] = lock
    if len(_LOCKS) > _LOCKS_CAP:
        for candidate_path, candidate in _LOCKS.items():
            if candidate_path == path:
                continue  # never evict the lock we are about to return
            if not candidate.locked():
                del _LOCKS[candidate_path]
                break
    return lock


class LedgerWriter:
    """Append-only writer for one member's ``ledger.jsonl``.

    Knows nothing about record semantics — the caller (``LLMClient``)
    builds the record; the writer serializes appends under the shared
    per-file lock and creates the parent directory on first write.
    """

    def __init__(self, path: Path):
        """Args:
        path: The ledger file (``paths.agent_ledger_file(agent_id)``)
        """
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    async def append(self, record: dict[str, Any]) -> None:
        """Append one record. Raises on I/O failure (the caller decides
        whether a bookkeeping failure is fatal — LLMClient logs and
        continues)."""
        async with _lock_for(self._path):
            self._path.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(self._path, "a", encoding="utf-8") as f:
                await f.write(json.dumps(record, ensure_ascii=False) + "\n")


async def read_ledger(path: Path) -> list[dict[str, Any]]:
    """Read all records from a ledger file, skipping malformed lines."""
    if not path.exists():
        return []
    try:
        async with aiofiles.open(path, encoding="utf-8") as f:
            content = await f.read()
    except Exception as e:
        logger.error("ledger_read_failed", file_path=str(path), error=str(e))
        return []

    records: list[dict[str, Any]] = []
    for line_num, line in enumerate(content.splitlines()):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if isinstance(record, dict):
                records.append(record)
        except json.JSONDecodeError as e:
            logger.warning(
                "ledger_parse_failed",
                file_path=str(path),
                line_number=line_num,
                error=str(e),
            )
    return records


def _int_field(record: dict[str, Any], field: str) -> int:
    value = record.get(field)
    return value if isinstance(value, int) else 0


def _new_summary() -> dict[str, Any]:
    return {
        "calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "duration_ms": 0,
        "cpu_ms": 0,
    }


def _fold(summary: dict[str, Any], record: dict[str, Any]) -> None:
    prompt = _int_field(record, "prompt_tokens")
    completion = _int_field(record, "completion_tokens")
    summary["calls"] += 1
    summary["prompt_tokens"] += prompt
    summary["completion_tokens"] += completion
    summary["total_tokens"] += prompt + completion
    summary["duration_ms"] += _int_field(record, "duration_ms")
    summary["cpu_ms"] += _int_field(record, "cpu_ms")


async def collect_agent_ledger(agent_id: str, *, tlon_paths: TlonPaths) -> dict[str, Any]:
    """Derive one member's consumption summary from its ledger file.

    Totals over every record plus per-``purpose`` and per-``model``
    splits (calls and tokens) — the by-model split observes how the
    different model bloodlines in a mixed-model society perform (ADR
    0014). Records without a purpose/model fold into ``"unknown"``.

    Returns:
        JSON-safe dict: agent_id, calls, prompt_tokens, completion_tokens,
        total_tokens, duration_ms, cpu_ms, by_purpose, by_model
    """
    records = await read_ledger(tlon_paths.agent_ledger_file(agent_id))
    summary = _new_summary()
    by_purpose: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    for record in records:
        _fold(summary, record)
        purpose = str(record.get("purpose") or "unknown")
        _fold(by_purpose.setdefault(purpose, _new_summary()), record)
        model = str(record.get("model") or "unknown")
        _fold(by_model.setdefault(model, _new_summary()), record)
    return {
        "agent_id": agent_id,
        **summary,
        "by_purpose": by_purpose,
        "by_model": by_model,
    }


async def collect_lineage_ledgers(*, tlon_paths: TlonPaths) -> dict[str, Any]:
    """Derive per-bloodline consumption from every member's ledger.

    The bloodline key is the member's ``AgentConfig.prototype``
    (``config/agent.yaml``); members without a prototype fold into
    ``"manual"``. Members with no ledger yet count zero. The member
    roster is re-read per call, so the report always sees the current
    society.

    Returns:
        JSON-safe dict keyed by bloodline name: prototype, members,
        calls, prompt_tokens, completion_tokens, total_tokens,
        duration_ms, cpu_ms, by_model (per-model split merged across the
        bloodline's members, issue )
    """
    configs = yaml_load_all(tlon_paths.tlon_home())
    lineages: dict[str, dict[str, Any]] = {}
    for agent_id, config in configs.items():
        key = config.prototype or "manual"
        lineage = lineages.setdefault(
            key,
            {"prototype": config.prototype, "members": [], **_new_summary(), "by_model": {}},
        )
        summary = await collect_agent_ledger(agent_id, tlon_paths=tlon_paths)
        lineage["members"].append(agent_id)
        for field in _new_summary():
            lineage[field] += summary[field]
        for model, bucket in summary["by_model"].items():
            merged = lineage["by_model"].setdefault(model, _new_summary())
            for field in _new_summary():
                merged[field] += bucket[field]
    return lineages
