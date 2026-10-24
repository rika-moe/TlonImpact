"""Read-only program run history views."""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from tlon.agents.program_history import program_events
from tlon.agents.program_store import ProgramStore
from tlon.base.paths import TlonPaths


def _completion_time(timestamp: str | None) -> float:
    """Compare persisted clocks across timezone offsets; undated sources sort first."""
    return datetime.fromisoformat(timestamp).timestamp() if timestamp else float("-inf")


class RunJournal:
    """Read and export history; execution belongs exclusively to ProgramStore."""

    def __init__(
        self, agent_id: str, tlon_paths: TlonPaths, *, program_store: ProgramStore | None = None
    ) -> None:
        self._agent_id = agent_id
        self._program_store = program_store
        self._program_database = tlon_paths.agent_program_database_file(agent_id)

    async def derive_context(self, run_id: str) -> list[dict[str, Any]]:
        """Re-derive the run context from the log (test assertions)."""
        context: list[dict[str, Any]] = []
        for event in await self.read_events(run_id):
            event_type = event.get("type")
            if event_type == "run/start":
                context = list(event.get("context") or [])
            elif event_type == "context/append":
                context.extend(event.get("messages") or [])
            elif event_type == "context/compact":
                context = list(event.get("context") or [])
        return context

    async def end_reason(self, run_id: str) -> str | None:
        """The committed ``run/end`` reason."""
        events = await self.read_events(run_id)
        return next((str(e["reason"]) for e in events if e["type"] == "run/end"), None)

    async def list_completed(self, started_after: datetime | None = None) -> list[str]:
        """Completed run ids, ordered by completion, optionally after a watermark.

        Entries filter by their terminal clock so late completion is visible. Undated records
        always pass. Only lifecycle metadata is read.
        """
        records = await asyncio.to_thread(self._program_metadata)
        completed = [
            (record["run_id"], record["ended_at"])
            for record in records
            if (record["completed"] or record["error"] is not None)
            and (
                started_after is None
                or record["ended_at"] is None
                or datetime.fromisoformat(record["ended_at"]) > started_after
            )
        ]
        return [
            source_id
            for source_id, _ in sorted(
                completed, key=lambda item: (_completion_time(item[1]), item[0])
            )
        ]

    async def count_completed(self, started_after: datetime | None = None) -> int:
        """Count completed runs with the same metadata watermark filter."""
        return len(await self.list_completed(started_after))

    async def latest_completed(self) -> str | None:
        """The latest run by committed end metadata, or None.

        Feeds the context builder's ``thread-recap`` section (ADR 0028).
        Only lifecycle metadata is queried; event bodies are not read.
        """
        candidates: list[tuple[str, str]] = []
        for record in await asyncio.to_thread(self._program_metadata):
            if record["completed"] or record["error"] is not None:
                candidates.append((record["ended_at"] or "", record["run_id"]))
        return (
            max(candidates, key=lambda item: (_completion_time(item[0]), item[1]))[1]
            if candidates
            else None
        )

    async def list_pending(self) -> list[str]:
        """Read unfinished evidence IDs for drain diagnostics; never restore them."""
        entries = await asyncio.to_thread(self._program_metadata)
        return sorted(
            [
                record["run_id"]
                for record in entries
                if not record["completed"] and record["error"] is None
            ]
        )

    async def read_events(self, run_id: str) -> list[dict[str, Any]]:
        """Read only the requested run's events, ordered by sequence."""
        records = await asyncio.to_thread(self._program_history, run_id)
        return next(
            (program_events(r, self._agent_id) for r in records if r["run"].run_id == run_id),
            [],
        )

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
        records = await asyncio.to_thread(self._program_history)

        def export() -> None:
            for record in records:
                source_id = record["run"].run_id
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
