"""Tests for the trust ladder (agents/trust.py, ).

Two levels, derived never stored: human-placed members are full by
construction; spawned members start on probation and turn full at their
first endorsement. The providers fail open — a derivation error reads as
full trust (never tightened).

Also covers the assembly-level tool subset: a probationary member's
``assemble_cognitive`` output lacks the four reproduction/evolution
tools (REPRODUCTION_TOOLS) but keeps the advisory reviewPrototype.
"""

from pathlib import Path
from typing import Any

import pytest

from tlon.agents.assembly import assemble_cognitive
from tlon.agents.trust import (
    ENDORSEMENT_THRESHOLD,
    REPRODUCTION_TOOLS,
    is_full_member,
    probation_provider,
    trust_provider,
)
from tlon.base.config import AgentConfig, LLMConfig, yaml_save
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox

MENTOR = "c-mentor"
ROOKIE = "c-rookie"
ROOKIE_ADDR = "c-rookie@tlon.internal"
OUTSIDER = "d-outsider"
OUTSIDER_ADDR = "d-outsider@tlon.internal"


@pytest.fixture
def tlon_paths(tmp_path: Path) -> TlonPaths:
    return TlonPaths(home=tmp_path)


def _write_agent(tlon_paths: TlonPaths, agent_id: str, *, spawned_by: str | None) -> None:
    yaml_save(
        AgentConfig(id=agent_id, name=agent_id, spawned_by=spawned_by),
        tlon_paths.tlon_home(),
    )


def _mail(mail_id: str, from_address: str, to: list[str], *, endorsing: str | None = None) -> Mail:
    return Mail(
        id=mail_id,
        from_address=from_address,
        to=to,
        cc=[],
        subject=None,
        content="正文",
        in_reply_to=None,
        thread_id=mail_id,
        forward_of=None,
        timestamp="2026-09-14T10:00:00",
        endorsing=endorsing,
    )


async def _endorse_rookie(tlon_paths: TlonPaths, endorser: str) -> None:
    """The outsider endorses the rookie's mail m1 (one endorsement)."""
    endorser_addr = f"{endorser}@tlon.internal"
    m1 = _mail("m1", ROOKIE_ADDR, [endorser_addr])
    await Mailbox(tlon_paths.member_mail_dir(ROOKIE)).append_sent(m1)
    endorser_box = Mailbox(tlon_paths.member_mail_dir(endorser))
    await endorser_box.append_inbox(m1)
    await endorser_box.append_sent(_mail("e1", endorser_addr, [ROOKIE_ADDR], endorsing="m1"))


class TestIsFullMember:
    async def test_human_placed_member_is_full(self, tlon_paths: TlonPaths) -> None:
        _write_agent(tlon_paths, ROOKIE, spawned_by=None)

        assert await is_full_member(ROOKIE, tlon_paths=tlon_paths) is True

    async def test_spawned_without_endorsement_is_probation(
        self, tlon_paths: TlonPaths
    ) -> None:
        _write_agent(tlon_paths, ROOKIE, spawned_by=MENTOR)
        _write_agent(tlon_paths, OUTSIDER, spawned_by=None)

        assert await is_full_member(ROOKIE, tlon_paths=tlon_paths) is False

    async def test_spawned_with_endorsement_turns_full(self, tlon_paths: TlonPaths) -> None:
        _write_agent(tlon_paths, ROOKIE, spawned_by=MENTOR)
        _write_agent(tlon_paths, OUTSIDER, spawned_by=None)
        await _endorse_rookie(tlon_paths, OUTSIDER)

        assert ENDORSEMENT_THRESHOLD == 1
        assert await is_full_member(ROOKIE, tlon_paths=tlon_paths) is True

    async def test_unknown_member_passes(self, tlon_paths: TlonPaths) -> None:
        """No agent.yaml on disk: not our member to gate — full."""
        assert await is_full_member("ghost", tlon_paths=tlon_paths) is True


class TestProviders:
    async def test_trust_provider_binds_paths(self, tlon_paths: TlonPaths) -> None:
        _write_agent(tlon_paths, ROOKIE, spawned_by=MENTOR)

        assert await trust_provider(tlon_paths)(ROOKIE) is False
        assert await probation_provider(tlon_paths)(ROOKIE) is True

    async def test_derivation_failure_fails_open_to_full(
        self, tlon_paths: TlonPaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A raising reputation derivation reads as full trust — trust
        bookkeeping must never gate anyone."""
        _write_agent(tlon_paths, ROOKIE, spawned_by=MENTOR)

        async def _boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise OSError("mail scan broke")

        monkeypatch.setattr("tlon.agents.trust.collect_reputation", _boom)

        assert await trust_provider(tlon_paths)(ROOKIE) is True
        assert await probation_provider(tlon_paths)(ROOKIE) is False


class TestReproductionTools:
    def test_reproduction_tools_inventory(self) -> None:
        expected = frozenset({"spawnAgent", "stopAgent", "writePrototype", "proposeBehavior"})
        assert expected == REPRODUCTION_TOOLS
        # reviewPrototype stays available to probationary members
        # (advisory only — approve remains a human decision).
        assert "reviewPrototype" not in REPRODUCTION_TOOLS


class _FakeAgentInfo:
    """AgentInfoProvider stand-in over a fixed config roster."""

    def __init__(self, configs: dict[str, AgentConfig]):
        self._configs = configs

    def get_all_ids(self) -> list[str]:
        return sorted(self._configs)

    def get_config(self, agent_id: str) -> AgentConfig | None:
        return self._configs.get(agent_id)


class TestAssemblyToolSubset:
    """The probation gate at assembly: the tool executor a probationary
    member gets lacks the reproduction/evolution four."""

    async def _assemble(self, tlon_paths: TlonPaths, *, spawned_by: str | None) -> Any:
        config = AgentConfig(
            id=ROOKIE,
            name=ROOKIE,
            spawned_by=spawned_by,
            llm=LLMConfig(model_name="test/model"),
        )
        _write_agent(tlon_paths, ROOKIE, spawned_by=spawned_by)
        return await assemble_cognitive(
            config,
            registry=None,  # type: ignore[arg-type]
            agent_info=_FakeAgentInfo({ROOKIE: config}),  # type: ignore[arg-type]
            tlon_paths=tlon_paths,
        )

    async def test_probation_member_lacks_reproduction_tools(
        self, tlon_paths: TlonPaths
    ) -> None:
        caps = await self._assemble(tlon_paths, spawned_by=MENTOR)

        names = set(caps.tool_executor.list_tools())
        assert names.isdisjoint(REPRODUCTION_TOOLS)
        assert "reviewPrototype" in names
        assert "listAgents" in names
        # The probation mentor cc rides along for the send paths.
        assert caps.mentor_cc == f"{MENTOR}@tlon.internal"

        await caps.tool_executor.shutdown()

    async def test_full_member_gets_everything(self, tlon_paths: TlonPaths) -> None:
        caps = await self._assemble(tlon_paths, spawned_by=None)

        names = set(caps.tool_executor.list_tools())
        assert names >= REPRODUCTION_TOOLS
        assert caps.mentor_cc is None

        await caps.tool_executor.shutdown()
