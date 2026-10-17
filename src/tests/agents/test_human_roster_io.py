"""Roster reads stay current without SQLite work on the event loop."""

import asyncio
import threading
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from tlon.agents.health import orphan_mail_provider
from tlon.agents.reputation import reputation_provider
from tlon.base.config import AgentInfoProvider
from tlon.base.paths import TlonPaths
from tlon.organization.mail.store import MailStore


@pytest.mark.parametrize("kind", ["reputation", "orphans"])
async def test_roster_provider_reads_fresh_humans_in_worker(tmp_path: Path, kind: str) -> None:
    paths = TlonPaths(home=tmp_path)
    store = MailStore(paths.mail_database_file())
    loop_thread = threading.get_ident()
    observed: list[list[str]] = []

    known_humans = store.known_human_ids

    def roster() -> list[str]:
        assert threading.get_ident() != loop_thread
        humans = known_humans()
        observed.append(humans)
        return humans

    async def query() -> None:
        if kind == "reputation":
            await reputation_provider(paths, roster)("alice")
        else:
            info = Mock(spec=AgentInfoProvider)
            info.get_all_ids.return_value = set()
            with patch.object(MailStore, "known_human_ids", side_effect=roster):
                await orphan_mail_provider(paths, info)()

    await query()
    await asyncio.to_thread(store.register_member, "human:bob")
    await query()
    assert observed == [[], ["human:bob"]]
