"""Multi-runtime contract: two ``build_runtime()`` instances coexist in one
process, each pinned to its own tlon home.

This is the pinning test for the paths-injection refactor (no more
``tlon.base.paths`` module singleton): every filesystem access in the
runtime flows through the explicitly injected ``TlonPaths``, so instance A
must leave zero traces in instance B's home — and vice versa. Any
process-level shared state that leaks across instances fails here.
"""

import uuid
from pathlib import Path

from tlon.api.arena import ArenaFacade
from tlon.api.bootstrap import build_runtime
from tlon.base.paths import TlonPaths
from tlon.base.settings import Settings
from tlon.organization.mail.store import MailStore


async def test_two_runtimes_coexist_in_one_process(tmp_path: Path) -> None:
    home_a = tmp_path / "home-a"
    home_b = tmp_path / "home-b"
    paths_a = TlonPaths(home=home_a)
    paths_b = TlonPaths(home=home_b)

    runtime_a = await build_runtime(paths_a, Settings.from_env())
    runtime_b = await build_runtime(paths_b, Settings.from_env())
    try:
        # Arena facade is wired into every runtime (issue #29 切片 3/3).
        assert isinstance(runtime_a.arena, ArenaFacade)
        assert isinstance(runtime_b.arena, ArenaFacade)
        # Bob is registered in A only; a legacy directory does not register a human.
        MailStore(paths_a.mail_database_file()).register_member("human:bob")

        token = uuid.uuid4().hex[:12]
        # Send through instance A's mail channel (facade → adapter manager,
        # no HTTP): alice writes to bob.
        await runtime_a.mail.send("alice", f"hello {token}", to=["bob"])

        store_a = MailStore(paths_a.mail_database_file())
        store_b = MailStore(paths_b.mail_database_file())
        assert [mail.content for mail in store_a.mails("human:bob")] == [f"hello {token}"]
        assert store_b.mails("human:bob") == []
        assert store_a.member_exists("human:alice")
        assert not store_b.member_exists("human:alice")
        assert paths_a.mail_database_file() != paths_b.mail_database_file()
    finally:
        await runtime_a.shutdown()
        await runtime_b.shutdown()
