"""Tests for the bloodline fitness derivation (agents/fitness.py, ).

Efficiency joins two derived signals — the metabolic ledger (tokens per
bloodline) and reputation (endorsements per member) — and ``tilt_for``
maps it to a quota multiplier clamped to [0.5, 2.0]. Cold start and
every failure path are neutral 1.0 (fail-open).
"""

from pathlib import Path
from typing import Any

import pytest

from tlon.agents.fitness import (
    TILT_MAX,
    TILT_MIN,
    insolvent_lineages,
    lineage_efficiency,
    solvency_provider,
    tilt_for,
    tilt_provider,
)
from tlon.base.config import AgentConfig, yaml_save
from tlon.base.ledger import LedgerWriter
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox

ALPHA_1 = "a-001"
ALPHA_2 = "a-002"
BETA_1 = "b-001"


def _mail(
    mail_id: str,
    from_address: str,
    to: list[str],
    *,
    endorsing: str | None = None,
) -> Mail:
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
        timestamp="2026-09-13T10:00:00",
        endorsing=endorsing,
    )


async def _seed_society(tlon_paths: TlonPaths) -> None:
    """Two bloodlines: alpha = {a-001, a-002} with 40 tokens and two
    endorsements received (one per member); beta = {b-001} with 5 tokens
    and no endorsements."""
    for agent_id, prototype in ((ALPHA_1, "alpha"), (ALPHA_2, "alpha"), (BETA_1, "beta")):
        yaml_save(
            AgentConfig(id=agent_id, name=agent_id, prototype=prototype), tlon_paths.tlon_home()
        )

    async def write_ledger(agent_id: str, prompt: int, completion: int) -> None:
        await LedgerWriter(tlon_paths.agent_ledger_file(agent_id)).append(
            {
                "ts": "2026-09-13T00:00:00+00:00",
                "event": "llm_call",
                "model": "openai/gpt-test",
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "duration_ms": 10,
                "cpu_ms": 1,
                "purpose": "think",
                "session_id": "s1",
            }
        )

    await write_ledger(ALPHA_1, 15, 5)  # 20 tokens
    await write_ledger(ALPHA_2, 15, 5)  # 20 tokens
    await write_ledger(BETA_1, 3, 2)  # 5 tokens

    # beta endorses one mail from each alpha member (the endorsed mail
    # sits in the endorser's inbox).
    beta = Mailbox(tlon_paths.member_mail_dir(BETA_1))
    for author, mail_id in ((ALPHA_1, "m1"), (ALPHA_2, "m2")):
        author_addr = f"{author}@tlon.internal"
        endorsed = _mail(mail_id, author_addr, [f"{BETA_1}@tlon.internal"])
        await Mailbox(tlon_paths.member_mail_dir(author)).append_sent(endorsed)
        await beta.append_inbox(endorsed)
        await beta.append_sent(
            _mail(f"e-{mail_id}", f"{BETA_1}@tlon.internal", [author_addr], endorsing=mail_id)
        )


@pytest.fixture
def tlon_paths(tmp_path: Path) -> TlonPaths:
    return TlonPaths(home=tmp_path)


class TestLineageEfficiency:
    async def test_value_over_tokens_per_bloodline(self, tlon_paths: TlonPaths) -> None:
        await _seed_society(tlon_paths)

        efficiencies = await lineage_efficiency(tlon_paths)

        # alpha: value = (1+1) + (1+1) = 4 over 40 tokens
        assert efficiencies["alpha"] == pytest.approx(0.1)
        # beta: value = 1 over 5 tokens
        assert efficiencies["beta"] == pytest.approx(0.2)

    async def test_endorsementless_bloodline_counts_members_only(
        self, tlon_paths: TlonPaths
    ) -> None:
        for agent_id in (ALPHA_1, ALPHA_2):
            yaml_save(
                AgentConfig(id=agent_id, name=agent_id, prototype="alpha"),
                tlon_paths.tlon_home(),
            )

        efficiencies = await lineage_efficiency(tlon_paths)

        # No ledger and no endorsements: value 2 over max(0, 1) token.
        assert efficiencies == {"alpha": 2.0}

    async def test_failure_returns_empty_fail_open(
        self, tlon_paths: TlonPaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _boom(**kwargs: Any) -> dict[str, Any]:
            raise OSError("corrupt home")

        monkeypatch.setattr("tlon.agents.fitness.collect_lineage_ledgers", _boom)

        assert await lineage_efficiency(tlon_paths) == {}


class TestTiltFor:
    def test_relative_to_mean(self) -> None:
        efficiencies = {"alpha": 0.1, "beta": 0.2}  # mean 0.15
        assert tilt_for(efficiencies, "alpha") == pytest.approx(2 / 3)
        assert tilt_for(efficiencies, "beta") == pytest.approx(4 / 3)

    def test_upper_clamp(self) -> None:
        efficiencies = {"hot": 10.0, "a": 1.0, "b": 1.0}  # mean 4, ratio 2.5
        assert tilt_for(efficiencies, "hot") == TILT_MAX

    def test_lower_clamp(self) -> None:
        efficiencies = {"cold": 1.0, "a": 10.0, "b": 10.0}  # mean 7, ratio 1/7
        assert tilt_for(efficiencies, "cold") == TILT_MIN

    def test_exact_boundary_not_clamped(self) -> None:
        efficiencies = {"x": 2.0, "y": 0.0}  # mean 1.0, ratio exactly 2.0
        assert tilt_for(efficiencies, "x") == 2.0

    def test_cold_start_is_neutral(self) -> None:
        assert tilt_for({}, "alpha") == 1.0
        assert tilt_for({"a": 0.0, "b": 0.0}, "a") == 1.0  # all zero
        assert tilt_for({"a": 1.0}, "ghost") == 1.0  # bloodline absent


class TestTiltProvider:
    async def test_binds_paths_and_maps_prototype(self, tlon_paths: TlonPaths) -> None:
        await _seed_society(tlon_paths)
        provider = tilt_provider(tlon_paths)

        assert await provider("alpha") == pytest.approx(2 / 3)
        assert await provider("beta") == pytest.approx(4 / 3)

    async def test_empty_home_is_neutral(self, tlon_paths: TlonPaths) -> None:
        provider = tilt_provider(tlon_paths)

        assert await provider("anything") == 1.0


async def _write_tokens(tlon_paths: TlonPaths, agent_id: str, tokens: int) -> None:
    await LedgerWriter(tlon_paths.agent_ledger_file(agent_id)).append(
        {
            "ts": "2026-09-13T00:00:00+00:00",
            "event": "llm_call",
            "model": "openai/gpt-test",
            "prompt_tokens": tokens,
            "completion_tokens": 0,
            "duration_ms": 10,
            "cpu_ms": 1,
            "purpose": "think",
            "session_id": "s1",
        }
    )


class TestInsolventLineages:
    """Solvency gate (): insolvent = total_tokens >= 100k (sample
    floor — below it the evidence is too thin to convict) AND efficiency
    < 0.25 × society mean (long-term outgoings exceed endorsed income)."""

    async def _seed(self, tlon_paths: TlonPaths) -> None:
        """alpha: 1 member, 200k tokens, value 1 — efficiency 5e-6.
        beta: 20 members × 5k tokens = 100k tokens, value 20 — 2e-4.
        Mean ≈ 1.03e-4, threshold ≈ 2.6e-5: alpha is insolvent, beta is
        not."""
        await _seed_tokens_society(
            tlon_paths, [("a-001", "alpha", 200_000)]
            + [(f"b-{i:03d}", "beta", 5_000) for i in range(1, 21)]
        )

    async def test_high_spend_low_efficiency_flagged(self, tlon_paths: TlonPaths) -> None:
        await self._seed(tlon_paths)

        assert await insolvent_lineages(tlon_paths) == {"alpha"}

    async def test_below_sample_floor_acquitted_even_when_least_efficient(
        self, tlon_paths: TlonPaths
    ) -> None:
        """gamma is the least efficient bloodline (1e-5 vs beta's 2e-4,
        below the 0.25×mean threshold) but spent only 99_999 tokens —
        under the sample floor, so presumption of innocence applies."""
        await _seed_tokens_society(
            tlon_paths,
            [("g-001", "gamma", 99_999)]
            + [(f"b-{i:03d}", "beta", 5_000) for i in range(1, 21)],
        )

        assert await insolvent_lineages(tlon_paths) == set()

    async def test_zero_consumption_empty(self, tlon_paths: TlonPaths) -> None:
        """No ledgers at all: no bloodline can reach the sample floor."""
        for agent_id, prototype in ((ALPHA_1, "alpha"), (BETA_1, "beta")):
            yaml_save(
                AgentConfig(id=agent_id, name=agent_id, prototype=prototype),
                tlon_paths.tlon_home(),
            )

        assert await insolvent_lineages(tlon_paths) == set()

    async def test_failure_returns_empty_fail_open(
        self, tlon_paths: TlonPaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _boom(**kwargs: Any) -> dict[str, Any]:
            raise OSError("corrupt home")

        monkeypatch.setattr("tlon.agents.fitness.collect_lineage_ledgers", _boom)

        assert await insolvent_lineages(tlon_paths) == set()


async def _seed_tokens_society(
    tlon_paths: TlonPaths, members: list[tuple[str, str, int]]
) -> None:
    """(agent_id, prototype, total_tokens) rows: one config + one ledger
    entry each; production = member existence only (no endorsements)."""
    for agent_id, prototype, tokens in members:
        yaml_save(
            AgentConfig(id=agent_id, name=agent_id, prototype=prototype),
            tlon_paths.tlon_home(),
        )
        await _write_tokens(tlon_paths, agent_id, tokens)


class TestSolvencyProvider:
    async def test_binds_paths_and_maps_prototype(self, tlon_paths: TlonPaths) -> None:
        await _seed_tokens_society(
            tlon_paths, [("a-001", "alpha", 200_000)]
            + [(f"b-{i:03d}", "beta", 5_000) for i in range(1, 21)]
        )
        provider = solvency_provider(tlon_paths)

        assert await provider("alpha") is False
        assert await provider("beta") is True

    async def test_unknown_bloodline_is_solvent(self, tlon_paths: TlonPaths) -> None:
        """A bloodline with no ledger is innocent by construction."""
        provider = solvency_provider(tlon_paths)

        assert await provider("ghost") is True
