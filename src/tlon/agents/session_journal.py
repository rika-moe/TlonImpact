"""Read-only legacy session history and module-program interaction views."""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from tlon.agents.program_history import PROGRAM_SOURCE_PREFIX, program_events
from tlon.agents.program_store import ProgramStore
from tlon.agents.session_store import SessionStore
from tlon.base.paths import TlonPaths


def _completion_time(timestamp: str | None) -> float:
    """Compare persisted clocks across timezone offsets; undated sources sort first."""
    return datetime.fromisoformat(timestamp).timestamp() if timestamp else float("-inf")


class SessionJournal:
    """Read and export history; execution belongs exclusively to ProgramStore."""

    def __init__(
        self, agent_id: str, tlon_paths: TlonPaths, *, program_store: ProgramStore | None = None
    ) -> None:
        self._agent_id = agent_id
        self._program_store = program_store
        self._program_database = tlon_paths.agent_program_database_file(agent_id)
        self._store = SessionStore(tlon_paths.agent_sessions_database_file(agent_id))

    async def derive_context(self, session_id: str) -> list[dict[str, Any]]:
        """Re-derive the session context from the log (test assertions)."""
        context: list[dict[str, Any]] = []
        for event in await self.read_events(session_id):
            event_type = event.get("type")
            if event_type == "session/start":
                context = list(event.get("context") or [])
            elif event_type == "context/append":
                context.extend(event.get("messages") or [])
            elif event_type == "context/compact":
                context = list(event.get("context") or [])
        return context

    async def end_reason(self, session_id: str) -> str | None:
        """The committed ``session/end`` reason, without reading events."""
        if session_id.startswith(PROGRAM_SOURCE_PREFIX):
            events = await self.read_events(session_id)
            return next((str(e["reason"]) for e in events if e["type"] == "session/end"), None)
        return await asyncio.to_thread(self._store.end_reason, session_id)

    async def list_completed(self, started_after: datetime | None = None) -> list[str]:
        """Completed session ids, ordered by completion, optionally after a watermark.

        Legacy sessions filter by their start clock; program entries filter by
        their terminal clock so late completion is visible. Undated records
        always pass. Only lifecycle metadata is read.
        """
        legacy = await asyncio.to_thread(self._store.completed_metadata, started_after)
        records = await asyncio.to_thread(self._program_metadata)
        completed = [(source_id, ended_at) for source_id, ended_at in legacy]
        completed.extend(
            (PROGRAM_SOURCE_PREFIX + record["run_id"], record["ended_at"])
            for record in records
            if (record["completed"] or record["error"] is not None)
            and (
                started_after is None
                or record["ended_at"] is None
                or datetime.fromisoformat(record["ended_at"]) > started_after
            )
        )
        return [
            source_id
            for source_id, _ in sorted(
                completed, key=lambda item: (_completion_time(item[1]), item[0])
            )
        ]

    async def count_completed(self, started_after: datetime | None = None) -> int:
        """Count completed sessions with the same metadata watermark filter."""
        return len(await self.list_completed(started_after))

    async def latest_completed(self) -> str | None:
        """The latest session by committed end metadata, or None.

        Feeds the context builder's ``thread-recap`` section (ADR 0028).
        Only lifecycle metadata is queried; event bodies are not read.
        """
        legacy = await asyncio.to_thread(self._store.latest_completed_metadata)
        candidates: list[tuple[str, str]] = []
        if legacy is not None:
            candidates.append((legacy[1], legacy[0]))
        for record in await asyncio.to_thread(self._program_metadata):
            if record["completed"] or record["error"] is not None:
                candidates.append(
                    (record["ended_at"] or "", PROGRAM_SOURCE_PREFIX + record["run_id"])
                )
        return (
            max(candidates, key=lambda item: (_completion_time(item[0]), item[1]))[1]
            if candidates
            else None
        )

    async def list_pending(self) -> list[str]:
        """Read unfinished evidence IDs for drain diagnostics; never restore them."""
        legacy = await asyncio.to_thread(self._store.unfinished_ids)
        entries = await asyncio.to_thread(self._program_metadata)
        return sorted(
            legacy
            + [
                PROGRAM_SOURCE_PREFIX + record["run_id"]
                for record in entries
                if not record["completed"] and record["error"] is None
            ]
        )

    async def read_events(self, session_id: str) -> list[dict[str, Any]]:
        """Read only the requested session's events, ordered by sequence."""
        if session_id.startswith(PROGRAM_SOURCE_PREFIX):
            run_id = session_id.removeprefix(PROGRAM_SOURCE_PREFIX)
            records = await asyncio.to_thread(self._program_history, run_id)
            return next(
                (program_events(r, self._agent_id) for r in records if r["run"].run_id == run_id),
                [],
            )
        return await asyncio.to_thread(self._store.events, session_id)

    def _program_metadata(self) -> list[dict[str, Any]]:
        if self._program_store is None:
            if not self._program_database.exists():
                return []
            self._program_store = ProgramStore(self._program_database)
        return self._program_store.entry_metadata()

    def _program_history(
        self, run_id: str | None = None, *, include_invocations: bool = True
    ) -> list[dict[str, Any]]:
        if self._program_store is None:
            if not self._program_database.exists():
                return []
            self._program_store = ProgramStore(self._program_database)
        return self._program_store.entry_history(run_id, include_invocations=include_invocations)

    async def export_jsonl(self, directory: Path) -> None:
        """Export reading artifacts including pending entries; never resume work."""
        await asyncio.to_thread(self._store.export_jsonl, directory)
        records = await asyncio.to_thread(self._program_history)

        def export() -> None:
            for record in records:
                source_id = PROGRAM_SOURCE_PREFIX + record["run"].run_id
                directory.mkdir(parents=True, exist_ok=True)
                destination = directory / (quote(source_id, safe="") + ".jsonl")
                destination.write_text(
                    "".join(
                        json.dumps(e, ensure_ascii=False) + "\n"
                        for e in program_events(record, self._agent_id)
                    ),
                    encoding="utf-8",
                )

        await asyncio.to_thread(export)
