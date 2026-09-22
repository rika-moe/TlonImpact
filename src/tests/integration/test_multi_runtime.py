"""Multi-runtime contract: two ``build_runtime()`` instances coexist in one
process, each pinned to its own tlon home.

This is the pinning test for the paths-injection refactor (no more
``tlon.base.paths`` module singleton): every filesystem access in the
runtime flows through the explicitly injected ``TlonPaths``, so instance A
must leave zero traces in instance B's home — and vice versa. Any
process-level shared state that leaks across instances fails here.
"""

import asyncio
import uuid
from pathlib import Path

from tlon.api.bootstrap import build_runtime
from tlon.base.paths import TlonPaths

DELIVERY_TIMEOUT = 10.0


async def _wait_for(predicate, timeout: float = DELIVERY_TIMEOUT) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("condition not met in time")
        await asyncio.sleep(0.05)


def _mail_dirs(home: Path) -> list[str]:
    """Member dirs carrying a mailbox under ``home`` (seed dirs excluded)."""
    return sorted(
        child.name
        for child in home.iterdir()
        if child.is_dir() and (child / "mail").is_dir()
    )


async def test_two_runtimes_coexist_in_one_process(tmp_path: Path) -> None:
    home_a = tmp_path / "home-a"
    home_b = tmp_path / "home-b"
    paths_a = TlonPaths(home=home_a)
    paths_b = TlonPaths(home=home_b)

    runtime_a = await build_runtime(paths_a)
    runtime_b = await build_runtime(paths_b)
    try:
        # bob is a known human in A only (mailbox exists → deliverable).
        paths_a.member_mail_dir("human:bob")

        token = uuid.uuid4().hex[:12]
        # Send through instance A's mail channel (facade → adapter manager,
        # no HTTP): alice writes to bob.
        await runtime_a.mail.send("alice", f"hello {token}", to=["bob"])

        inbox_bob = home_a / "human-bob" / "mail" / "inbox.jsonl"
        await _wait_for(lambda: inbox_bob.exists() and token in inbox_bob.read_text())

        # The mail landed in A's layout only: sender + recipient mailboxes
        # exist under home_a (plus Adam's built-in guardian inbox), and
        # instance B's home carries no member mail beyond its own adam.
        assert _mail_dirs(home_a) == ["adam", "human-alice", "human-bob"]
        assert _mail_dirs(home_b) == ["adam"]
    finally:
        await runtime_a.shutdown()
        await runtime_b.shutdown()
