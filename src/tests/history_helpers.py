"""Construct archived legacy evidence in tests; production journals are read-only."""

import asyncio
from typing import Any

from tlon.agents.session_journal import SessionJournal


async def append_history(
    journal: SessionJournal, session_id: str, event_type: str, **payload: Any
) -> None:
    """Write a legacy fixture through its SQLite store, outside the history API."""
    await asyncio.to_thread(journal._store.append, session_id, event_type, payload)
