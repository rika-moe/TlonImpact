"""Roster reads stay current without SQLite work on the event loop."""

import asyncio
import threading
from pathlib import Path

import pytest

from tlon.agents.health import orphan_mail_provider
from tlon.agents.reputation import reputation_provider
from tlon.base.paths import TlonPaths
from tlon.organization.mail.store import MailStore


@pytest.mark.parametrize("kind", ["reputation", "orphans"])
async def test_roster_provider_reads_fresh_humans_in_worker(tmp_path: Path, kind: str) -> None:
    paths = TlonPaths(home=tmp_path)
    store = MailStore(paths.mail_database_file())
    loop_thread = threading.get_ident()
    observed: list[list[str]] = []

    def roster() -> list[str]:
        assert threading.get_ident() != loop_thread
        humans = store.known_human_ids()
        observed.append(humans)
        return humans

    async def query() -> None:
        if kind == "reputation":
            await reputation_provider(paths, roster)("alice")
        else:
            await orphan_mail_provider(paths, roster)()

    await query()
    await asyncio.to_thread(store.register_member, "human:bob")
    await query()
    assert observed == [[], ["human:bob"]]
