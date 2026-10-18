"""Tests for the society policy context (agents/policy.py, ADR 0022).

Three layers: the default policy's hook semantics (an exact replica of
the pre-migration fitness.py / trust.py formulas, asserted against
hand-computed values), policy.tlon loading (per-hook override onto the
default env, wholesale fallback on any failure), and the providers
(same fail-open discipline as the pre-migration providers).
"""

from pathlib import Path
from typing import Any

import pytest

from tlon.agents.assembly import REPRODUCTION_TOOLS, assemble_member_capabilities
from tlon.agents.policy import (
    TILT_NEUTRAL,
    PolicyValidationError,
    _parse_policy,
    load_policy,
    policy_probation_provider,
    policy_solvency_provider,
    policy_tilt_provider,
    policy_trust_provider,
)
from tlon.base.config import AgentConfig, LLMConfig, yaml_save
from tlon.base.paths import TlonPaths
from tlon.organization.mail.mail import Mail
from tlon.organization.mail.mailbox import Mailbox


@pytest.fixture
def tlon_paths(tmp_path: Path) -> TlonPaths:
    return TlonPaths(home=tmp_path)


def _lineage(name: str, total_tokens: int, endorsements: list[int]) -> dict[str, Any]:
    return {
        "name": name,
        "total_tokens": total_tokens,
        "members": [
            {"id": f"{name}-{i}", "endorsements_received": e} for i, e in enumerate(endorsements)
        ],
    }


#: a: value 10 over 200k tokens (eff 5e-5); b: value 1 over 200k (eff 5e-6);
#: society mean 2.75e-5.
LINEAGES = [
    _lineage("a", 200_000, [9]),
    _lineage("b", 200_000, [0]),
]


class TestDefaultPolicySemantics:
    """The default policy reproduces fitness.py / trust.py exactly."""

    def test_tilt_relative_to_society_mean(self, tlon_paths: TlonPaths) -> None:
        policy = load_policy(tlon_paths)
        assert policy.call("tilt", LINEAGES[0], LINEAGES) == pytest.approx(10 / 5.5)
        # the inefficient bloodline's raw ratio 0.1818… clamps to the 0.5 floor
        assert policy.call("tilt", LINEAGES[1], LINEAGES) == 0.5

    def test_tilt_cold_start_is_neutral(self, tlon_paths: TlonPaths) -> None:
        policy = load_policy(tlon_paths)
        assert policy.call("tilt", None, LINEAGES) == TILT_NEUTRAL
        assert policy.call("tilt", None, []) == TILT_NEUTRAL
        zeroed = [_lineage("a", 100, [0]), _lineage("b", 100, [0])]
        # equal efficiencies → ratio 1.0 for everyone
        assert policy.call("tilt", zeroed[0], zeroed) == pytest.approx(1.0)

    def test_solvency_gate(self, tlon_paths: TlonPaths) -> None:
        policy = load_policy(tlon_paths)
        assert policy.call("solvent?", LINEAGES[0], LINEAGES) is True
        # b's efficiency 5e-6 < 0.25 × mean 2.75e-5 → insolvent
        assert policy.call("solvent?", LINEAGES[1], LINEAGES) is False

    def test_solvency_presumption_of_innocence(self, tlon_paths: TlonPaths) -> None:
        policy = load_policy(tlon_paths)
        small = _lineage("c", 50_000, [0])  # below the 100k sample floor
        assert policy.call("solvent?", small, LINEAGES) is True
        assert policy.call("solvent?", None, LINEAGES) is True

    def test_full_member_predicate(self, tlon_paths: TlonPaths) -> None:
        policy = load_policy(tlon_paths)
        human = {"id": "h", "spawned_by": None, "endorsements_received": 0}
        rookie = {"id": "r", "spawned_by": "m", "endorsements_received": 0}
        endorsed = {"id": "e", "spawned_by": "m", "endorsements_received": 1}
        assert policy.call("full-member?", human) is True
        assert policy.call("full-member?", rookie) is False
        assert policy.call("full-member?", endorsed) is True


class TestPolicyFile:
    def test_missing_file_means_default(self, tlon_paths: TlonPaths) -> None:
        policy = load_policy(tlon_paths)
        assert policy.call("tilt", None, []) == TILT_NEUTRAL

    def test_override_a_single_hook(self, tlon_paths: TlonPaths) -> None:
        tlon_paths.policy_file().write_text(
            "{:version 1 :policy [(defn tilt [subject lineages] 42)]}",
            encoding="utf-8",
        )
        policy = load_policy(tlon_paths)
        assert policy.call("tilt", None, []) == 42
        # hooks not overridden keep the default implementation
        assert policy.call("solvent?", None, []) is True
        assert (
            policy.call("full-member?", {"id": "r", "spawned_by": "m", "endorsements_received": 0})
            is False
        )

    def test_override_may_reuse_default_helpers(self, tlon_paths: TlonPaths) -> None:
        """The file's forms thread into the default env: efficiency & co.
        remain visible to an overriding hook."""
        tlon_paths.policy_file().write_text(
            "{:version 1 :policy [(defn tilt [subject lineages]"
            " (if (= nil subject) 1.0 (* 10 (efficiency subject))))]}",
            encoding="utf-8",
        )
        policy = load_policy(tlon_paths)
        assert policy.call("tilt", LINEAGES[0], LINEAGES) == pytest.approx(10 * 5e-5)

    def test_unparseable_file_falls_back_wholesale(self, tlon_paths: TlonPaths) -> None:
        tlon_paths.policy_file().write_text("{{{ not edn", encoding="utf-8")
        policy = load_policy(tlon_paths)
        assert policy.call("tilt", LINEAGES[1], LINEAGES) == 0.5

    def test_bad_version_falls_back(self, tlon_paths: TlonPaths) -> None:
        tlon_paths.policy_file().write_text("{:version 2 :policy []}", encoding="utf-8")
        policy = load_policy(tlon_paths)
        assert policy.call("tilt", LINEAGES[1], LINEAGES) == 0.5

    def test_unknown_vocabulary_falls_back(self, tlon_paths: TlonPaths) -> None:
        tlon_paths.policy_file().write_text(
            "{:version 1 :policy [(defn tilt [s l] (frobnicate s))]}", encoding="utf-8"
        )
        policy = load_policy(tlon_paths)
        assert policy.call("tilt", LINEAGES[1], LINEAGES) == 0.5

    def test_failing_eval_falls_back_without_pollution(self, tlon_paths: TlonPaths) -> None:
        """A file whose forms raise mid-load is rejected wholesale: the
        default env is rebuilt pristine, never the half-threaded one.
        (The raising form sits at the top level — a defn body is not
        evaluated at load.)"""
        tlon_paths.policy_file().write_text(
            "{:version 1 :policy [(defn tilt [s l] 99) (/ 1 0)]}",
            encoding="utf-8",
        )
        policy = load_policy(tlon_paths)
        # tilt's override (99) must NOT survive the file's rejection
        assert policy.call("tilt", LINEAGES[1], LINEAGES) == 0.5

    def test_parse_rejects_bad_shapes(self) -> None:
        with pytest.raises(PolicyValidationError, match="top-level map"):
            _parse_policy("[1 2 3]")
        with pytest.raises(PolicyValidationError, match=":policy must be a vector"):
            _parse_policy("{:version 1 :policy 42}")
        with pytest.raises(PolicyValidationError, match="non-empty forms"):
            _parse_policy("{:version 1 :policy [42]}")


class TestProviders:
    async def test_empty_society(self, tlon_paths: TlonPaths) -> None:
        assert await policy_tilt_provider(tlon_paths)("anything") == TILT_NEUTRAL
        assert await policy_solvency_provider(tlon_paths)("anything") is True
        assert await policy_trust_provider(tlon_paths)("ghost") is True

    async def test_tilt_provider_uses_policy_file(self, tlon_paths: TlonPaths) -> None:
        tlon_paths.policy_file().write_text(
            "{:version 1 :policy [(defn tilt [subject lineages] 42)]}", encoding="utf-8"
        )
        assert await policy_tilt_provider(tlon_paths)("anything") == 42.0

    async def test_tilt_provider_rejects_non_number(self, tlon_paths: TlonPaths) -> None:
        tlon_paths.policy_file().write_text(
            '{:version 1 :policy [(defn tilt [s l] "boom")]}', encoding="utf-8"
        )
        assert await policy_tilt_provider(tlon_paths)("anything") == TILT_NEUTRAL

    async def test_hook_failure_fails_open(self, tlon_paths: TlonPaths) -> None:
        """A runaway hook (recursion bomb → depth cap) reads as the safe
        default, never as a gate."""
        tlon_paths.policy_file().write_text(
            "{:version 1 :policy [(defn tilt [s l] (tilt s l))]}", encoding="utf-8"
        )
        assert await policy_tilt_provider(tlon_paths)("anything") == TILT_NEUTRAL

    async def test_solvency_failure_fails_open(self, tlon_paths: TlonPaths) -> None:
        tlon_paths.policy_file().write_text(
            "{:version 1 :policy [(defn solvent? [s l] (solvent? s l))]}", encoding="utf-8"
        )
        assert await policy_solvency_provider(tlon_paths)("anything") is True

    # ── trust provider over the real filesystem ──

    def _write_agent(self, tlon_paths: TlonPaths, agent_id: str, *, spawned_by: str | None) -> None:
        yaml_save(
            AgentConfig(id=agent_id, name=agent_id, spawned_by=spawned_by),
            tlon_paths.tlon_home(),
        )

    async def _endorse(self, tlon_paths: TlonPaths, target: str, endorser: str) -> None:
        target_addr = f"{target}@tlon.internal"
        endorser_addr = f"{endorser}@tlon.internal"
        m1 = Mail(
            id="m1",
            from_address=target_addr,
            to=[endorser_addr],
            cc=[],
            subject=None,
            content="正文",
            in_reply_to=None,
            thread_id="m1",
            forward_of=None,
            timestamp="2026-09-28T10:00:00",
            endorsing=None,
        )
        await Mailbox(tlon_paths.mail_database_file(), target).append_sent(m1)
        endorser_box = Mailbox(tlon_paths.mail_database_file(), endorser)
        await endorser_box.append_inbox(m1)
        await endorser_box.append_sent(
            Mail(
                id="e1",
                from_address=endorser_addr,
                to=[target_addr],
                cc=[],
                subject=None,
                content="赞",
                in_reply_to=None,
                thread_id="e1",
                forward_of=None,
                timestamp="2026-09-28T10:01:00",
                endorsing="m1",
            )
        )

    async def test_trust_provider_spawned_member_on_probation(self, tlon_paths: TlonPaths) -> None:
        self._write_agent(tlon_paths, "rookie", spawned_by="mentor")
        assert await policy_trust_provider(tlon_paths)("rookie") is False

    async def test_trust_provider_endorsed_turns_full(self, tlon_paths: TlonPaths) -> None:
        self._write_agent(tlon_paths, "rookie", spawned_by="mentor")
        self._write_agent(tlon_paths, "outsider", spawned_by=None)
        await self._endorse(tlon_paths, "rookie", "outsider")
        assert await policy_trust_provider(tlon_paths)("rookie") is True

    async def test_trust_derivation_failure_fails_open(
        self, tlon_paths: TlonPaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._write_agent(tlon_paths, "rookie", spawned_by="mentor")

        async def _boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise OSError("mail scan broke")

        monkeypatch.setattr("tlon.agents.policy.collect_reputation", _boom)
        assert await policy_trust_provider(tlon_paths)("rookie") is True


class TestProbationProvider:
    """The SpawnManager-side predicate: negated trust, fail-open flips."""

    async def test_probation_negates_trust(self, tlon_paths: TlonPaths) -> None:
        yaml_save(
            AgentConfig(id="rookie", name="rookie", spawned_by="mentor"),
            tlon_paths.tlon_home(),
        )
        assert await policy_probation_provider(tlon_paths)("rookie") is True

    async def test_derivation_failure_reads_as_not_probation(
        self, tlon_paths: TlonPaths, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml_save(
            AgentConfig(id="rookie", name="rookie", spawned_by="mentor"),
            tlon_paths.tlon_home(),
        )

        async def _boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
            raise OSError("mail scan broke")

        monkeypatch.setattr("tlon.agents.policy.collect_reputation", _boom)
        assert await policy_probation_provider(tlon_paths)("rookie") is False


class TestReproductionTools:
    def test_reproduction_tools_inventory(self) -> None:
        expected = frozenset(
            {"spawnAgent", "writePrototype", "proposeBehavior", "proposeCapability"}
        )
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
    """The probation gate at assembly (driven by the policy's
    ``full-member?`` hook): a probationary member's tool executor lacks
    the reproduction/evolution four."""

    async def _assemble(self, tlon_paths: TlonPaths, *, spawned_by: str | None) -> Any:
        config = AgentConfig(
            id="rookie",
            name="rookie",
            spawned_by=spawned_by,
            llm=LLMConfig(model_name="test/model"),
        )
        yaml_save(config, tlon_paths.tlon_home())
        return await assemble_member_capabilities(
            config,
            registry=None,  # type: ignore[arg-type]
            agent_info=_FakeAgentInfo({"rookie": config}),  # type: ignore[arg-type]
            tlon_paths=tlon_paths,
        )

    async def test_probation_member_lacks_reproduction_tools(self, tlon_paths: TlonPaths) -> None:
        caps = await self._assemble(tlon_paths, spawned_by="mentor")

        names = set(caps.tool_executor.list_tools())
        assert names.isdisjoint(REPRODUCTION_TOOLS)
        assert "reviewPrototype" in names
        assert "listMembers" in names
        # The probation mentor cc rides along for the send paths.
        assert caps.mentor_cc == "mentor@tlon.internal"

        await caps.tool_executor.shutdown()

    async def test_full_member_gets_everything(self, tlon_paths: TlonPaths) -> None:
        caps = await self._assemble(tlon_paths, spawned_by=None)

        names = set(caps.tool_executor.list_tools())
        assert names >= REPRODUCTION_TOOLS
        assert caps.mentor_cc is None

        await caps.tool_executor.shutdown()
