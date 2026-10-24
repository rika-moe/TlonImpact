"""Conflict judgment + invalidation without deletion (ADR 0017 stage 3).

Pins the claim lifecycle: both write paths (batch distillation and
``remember_fact``) judge new claims against similar active old claims;
SUPERSEDE verdicts invalidate the old claims in place (status /
superseded_by / valid_to — never deleted); and every failure mode of
the judgment (no index, no LLM, call failure, unparseable verdicts)
degrades to pure append. All LLM and embedding traffic is faked — no
network.
"""

import json
from pathlib import Path
from typing import Any

from tlon.base.config import LLMConfig
from tlon.base.llm_client import LLMResponse
from tlon.memory.distiller import ConflictVerdict, Distiller
from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig, RunProjection

_DIM = 4


def _axis(i: int) -> list[float]:
    """A unit vector along one axis — similarities are exact."""
    return [1.0 if j == i else 0.0 for j in range(_DIM)]


class FakeEmbedder:
    """Deterministic embedder: declared texts map to declared vectors,
    everything else to the zero vector."""

    def __init__(self, vectors: dict[str, list[float]], model_name: str = "fake/embed-4"):
        self._vectors = vectors
        self.model_name = model_name

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vectors.get(t, [0.0] * _DIM) for t in texts]


class FakeLLMClient:
    """Scripted chat(): returns queued contents in order, records calls."""

    def __init__(self, contents: list[str | None]):
        self._contents = list(contents)
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> LLMResponse:
        self.calls.append({"system_prompt": system_prompt, "messages": messages})
        return LLMResponse(content=self._contents.pop(0))


class FakeDistiller:
    """Duck-typed Distiller: scripted distill output + judge verdicts."""

    def __init__(
        self,
        distill_output: list[dict[str, Any]],
        verdicts: list[ConflictVerdict] | None = None,
        judge_error: Exception | None = None,
    ) -> None:
        self._distill_output = distill_output
        self._verdicts = verdicts
        self._judge_error = judge_error
        self.judge_calls: list[tuple[list[str], list[dict[str, Any]]]] = []

    async def distill(self, runs: list[RunProjection]) -> list[dict[str, Any]]:
        return self._distill_output

    async def judge_conflicts(
        self, new_claims: list[str], candidates: list[dict[str, Any]]
    ) -> list[ConflictVerdict] | None:
        self.judge_calls.append((new_claims, candidates))
        if self._judge_error is not None:
            raise self._judge_error
        return self._verdicts


def _distiller(llm: FakeLLMClient) -> Distiller:
    """A Distiller whose network client is swapped for the fake."""
    distiller = Distiller(LLMConfig(model_name="fake/model"))
    distiller.llm_client = llm  # type: ignore[assignment]
    return distiller


def _manager(tmp_path: Path, embedder: Any | None = None) -> MemoryManager:
    return MemoryManager("test-agent", MemoryConfig(), data_dir=tmp_path, embedder=embedder)


async def _seed(manager: MemoryManager, claims: list[dict[str, Any]]) -> MemoryManager:
    """Seed claims (bypassing the index write path), then load — the
    load-time index build puts them into the index."""
    for claim in claims:
        await manager.claims.add_claim(claim)
    await manager.load()
    return manager


# ===== Distiller.judge_conflicts =====


class TestJudgeConflicts:
    async def test_prompt_carries_candidate_ids_dates_and_numbered_new_claims(
        self,
    ) -> None:
        llm = FakeLLMClient(['{"verdicts": [{"index": 1, "action": "ADD"}]}'])
        candidates = [
            {"id": "old-1", "timestamp": "2026-09-10T08:00:00", "text": "用户喜欢手冲咖啡"},
        ]

        verdicts = await _distiller(llm).judge_conflicts(["用户改喝美式"], candidates)

        assert verdicts == [ConflictVerdict()]
        (call,) = llm.calls
        user_content = call["messages"][0]["content"]
        assert "id: old-1（2026-09-10）用户喜欢手冲咖啡" in user_content
        assert "1. 用户改喝美式" in user_content

    async def test_supersede_verdict_parsed(self) -> None:
        llm = FakeLLMClient(
            ['{"verdicts": [{"index": 1, "action": "SUPERSEDE", "superseded_ids": ["old-1"]}]}']
        )
        candidates = [{"id": "old-1", "text": "x"}]

        verdicts = await _distiller(llm).judge_conflicts(["y"], candidates)

        assert verdicts == [ConflictVerdict(action="supersede", superseded_ids=["old-1"])]

    async def test_markdown_wrapped_json_is_tolerated(self) -> None:
        llm = FakeLLMClient(
            ['思考过程……\n```json\n{"verdicts": [{"index": 1, "action": "ADD"}]}\n```']
        )

        verdicts = await _distiller(llm).judge_conflicts(["x"], [{"id": "old-1"}])

        assert verdicts == [ConflictVerdict()]

    async def test_malformed_verdicts_degrade_to_add(self) -> None:
        """Garbage entries, unknown actions, and hallucinated supersede
        ids all degrade the affected new claim to ADD — never invalidate
        on a guess."""
        llm = FakeLLMClient(
            [
                json.dumps(
                    {
                        "verdicts": [
                            "garbage",
                            {"index": 2, "action": "DELETE", "superseded_ids": ["old-1"]},
                            {"index": 3, "action": "SUPERSEDE", "superseded_ids": ["fake-id"]},
                            {"index": 4, "action": "SUPERSEDE", "superseded_ids": ["old-1"]},
                        ]
                    },
                    ensure_ascii=False,
                )
            ]
        )
        candidates = [{"id": "old-1", "text": "x"}]

        verdicts = await _distiller(llm).judge_conflicts(["a", "b", "c", "d"], candidates)

        assert verdicts == [
            ConflictVerdict(),
            ConflictVerdict(),
            ConflictVerdict(),
            ConflictVerdict(action="supersede", superseded_ids=["old-1"]),
        ]

    async def test_unparseable_response_returns_none(self) -> None:
        llm = FakeLLMClient(["这不是 JSON"])

        assert await _distiller(llm).judge_conflicts(["x"], [{"id": "old-1"}]) is None

    async def test_missing_verdicts_key_returns_none(self) -> None:
        llm = FakeLLMClient(['{"claims": []}'])

        assert await _distiller(llm).judge_conflicts(["x"], [{"id": "old-1"}]) is None

    async def test_empty_response_returns_none(self) -> None:
        llm = FakeLLMClient([None])

        assert await _distiller(llm).judge_conflicts(["x"], [{"id": "old-1"}]) is None

    async def test_llm_error_returns_none(self) -> None:
        class _Boom:
            async def chat(self, **kwargs: Any) -> LLMResponse:
                raise RuntimeError("llm down")

        distiller = Distiller(LLMConfig(model_name="fake/model"))
        distiller.llm_client = _Boom()  # type: ignore[assignment]

        assert await distiller.judge_conflicts(["x"], [{"id": "old-1"}]) is None

    async def test_missing_verdict_entries_default_to_add(self) -> None:
        """The model judged only some of the batch: the rest are ADD."""
        llm = FakeLLMClient(['{"verdicts": [{"index": 2, "action": "ADD"}]}'])

        verdicts = await _distiller(llm).judge_conflicts(["a", "b", "c"], [{"id": "o"}])

        assert verdicts == [ConflictVerdict(), ConflictVerdict(), ConflictVerdict()]


# ===== Manager: remember_fact conflict path =====


class TestRememberFactConflicts:
    async def test_contradicted_old_claim_is_superseded_not_deleted(self, tmp_path: Path) -> None:
        """memory-remember goes through the same conflict judgment: the old
        claim is invalidated in place (auditable), the new one appends."""
        old_text = "用户喜欢手冲咖啡"
        new_text = "用户改喝美式了"
        embedder = FakeEmbedder({old_text: _axis(0), new_text: _axis(0)})
        manager = await _seed(
            _manager(tmp_path, embedder),
            [{"id": "c-old", "text": old_text, "timestamp": "2026-09-10T08:00:00"}],
        )
        fake = FakeDistiller(
            [],
            verdicts=[ConflictVerdict(action="supersede", superseded_ids=["c-old"])],
        )
        manager.distiller = fake  # type: ignore[assignment]

        new_id = await manager.remember_fact(new_text)

        assert fake.judge_calls
        judged_texts, candidates = fake.judge_calls[0]
        assert judged_texts == [new_text]
        assert [c["id"] for c in candidates] == ["c-old"]

        old = await manager.claims.get_claim("c-old")
        assert old is not None  # invalidated, not deleted
        assert old["status"] == "superseded"
        assert old["superseded_by"] == new_id
        assert old["valid_to"]
        assert [c["id"] for c in await manager.claims.get_all_claims()] == [new_id]

    async def test_no_similar_old_claim_skips_judgment(self, tmp_path: Path) -> None:
        """Below the similarity threshold the new claim is an obvious
        ADD — the LLM judgment call is not even made (cost control)."""
        old_text = "项目部署在东京"
        new_text = "用户喜欢手冲咖啡"
        embedder = FakeEmbedder({old_text: _axis(0), new_text: _axis(1)})  # orthogonal
        manager = await _seed(
            _manager(tmp_path, embedder),
            [{"id": "c-old", "text": old_text, "timestamp": "2026-09-10T08:00:00"}],
        )
        fake = FakeDistiller([], verdicts=[])
        manager.distiller = fake  # type: ignore[assignment]

        await manager.remember_fact(new_text)

        assert fake.judge_calls == []
        assert len(await manager.claims.get_all_claims()) == 2

    async def test_config_threshold_gates_judgment(self, tmp_path: Path) -> None:
        """conflict.similarity_threshold is a live tuning knob: a pair at
        0.8 similarity is judged under the default 0.75 but ADDs directly
        once the threshold is raised to 0.9."""
        old_text = "项目部署在东京"
        new_text = "项目部署在大阪"

        async def judge_call_count(threshold: float, sub_dir: str) -> int:
            embedder = FakeEmbedder({old_text: [1.0, 0, 0, 0], new_text: [0.8, 0.6, 0, 0]})
            manager = _manager(tmp_path / sub_dir, embedder)
            manager.config.conflict_similarity_threshold = threshold
            await _seed(
                manager,
                [{"id": "c-old", "text": old_text, "timestamp": "2026-09-10T08:00:00"}],
            )
            fake = FakeDistiller([], verdicts=[ConflictVerdict()])
            manager.distiller = fake  # type: ignore[assignment]
            await manager.remember_fact(new_text)
            return len(fake.judge_calls)

        assert await judge_call_count(0.75, "default-threshold") == 1
        assert await judge_call_count(0.9, "raised-threshold") == 0


# ===== Manager: batch distillation conflict path =====


class TestDistillationConflicts:
    async def test_mixed_verdicts_supersede_and_append(self, tmp_path: Path) -> None:
        old_text = "部署脚本在 scripts/deploy.sh"
        moved_text = "部署脚本已迁移到 scripts/release.sh"
        fresh_text = "用户偏好深色主题"
        embedder = FakeEmbedder({old_text: _axis(0), moved_text: _axis(0), fresh_text: _axis(1)})
        manager = await _seed(
            _manager(tmp_path, embedder),
            [{"id": "c-old", "text": old_text, "timestamp": "2026-09-10T08:00:00"}],
        )
        fake = FakeDistiller(
            [{"text": moved_text, "importance": 0.7}, {"text": fresh_text, "importance": 0.4}],
            verdicts=[
                ConflictVerdict(action="supersede", superseded_ids=["c-old"]),
                ConflictVerdict(),
            ],
        )
        manager.distiller = fake  # type: ignore[assignment]
        projections = [RunProjection(run_id="s1", trigger_content="q")]

        claim_count = await manager._distill_runs(projections)

        assert claim_count == 2
        old = await manager.claims.get_claim("c-old")
        assert old is not None and old["status"] == "superseded"
        active_texts = [c["text"] for c in await manager.claims.get_all_claims()]
        assert active_texts == [moved_text, fresh_text]
        superseding = await manager.claims.get_claim(old["superseded_by"])
        assert superseding is not None and superseding["text"] == moved_text


# ===== Degradation ladder: judgment is an enhancement, never a blocker =====


class TestDegradation:
    async def test_no_embedder_appends_without_judgment(self, tmp_path: Path) -> None:
        manager = await _seed(
            _manager(tmp_path, None),
            [{"id": "c-old", "text": "旧记忆", "timestamp": "2026-09-10T08:00:00"}],
        )
        fake = FakeDistiller([], verdicts=[])
        manager.distiller = fake  # type: ignore[assignment]

        await manager.remember_fact("新记忆")

        assert fake.judge_calls == []
        assert len(await manager.claims.get_all_claims()) == 2

    async def test_no_distiller_appends_without_judgment(self, tmp_path: Path) -> None:
        """distiller=None (no LLM configured): conflict judgment does not
        exist — the write is a plain append."""
        text = "用户喜欢手冲咖啡"
        manager = await _seed(
            _manager(tmp_path, FakeEmbedder({text: _axis(0)})),
            [{"id": "c-old", "text": text, "timestamp": "2026-09-10T08:00:00"}],
        )
        assert manager.distiller is None

        await manager.remember_fact(text)

        assert len(await manager.claims.get_all_claims()) == 2

    async def test_judgment_failure_appends_everything(self, tmp_path: Path) -> None:
        """The judge call raising must not lose or block the write."""
        old_text = "用户喜欢手冲咖啡"
        new_text = "用户改喝美式了"
        manager = await _seed(
            _manager(tmp_path, FakeEmbedder({old_text: _axis(0), new_text: _axis(0)})),
            [{"id": "c-old", "text": old_text, "timestamp": "2026-09-10T08:00:00"}],
        )
        fake = FakeDistiller(
            [{"text": new_text, "importance": 0.7}],
            judge_error=RuntimeError("llm down"),
        )
        manager.distiller = fake  # type: ignore[assignment]
        projections = [RunProjection(run_id="s1", trigger_content="q")]

        claim_count = await manager._distill_runs(projections)

        assert claim_count == 1
        assert fake.judge_calls  # judgment was attempted — and failed
        old = await manager.claims.get_claim("c-old")
        assert old is not None and old.get("status", "active") == "active"
        assert len(await manager.claims.get_all_claims()) == 2

    async def test_fallback_claims_degrade_to_pure_append_when_llm_down(
        self, tmp_path: Path
    ) -> None:
        """The mechanical fallback claims (produced when the LLM is
        down) walk the same append path — judgment is equally down, so
        they simply append."""
        manager = await _seed(
            _manager(tmp_path, FakeEmbedder({})),
            [{"id": "c-old", "text": "旧记忆", "timestamp": "2026-09-10T08:00:00"}],
        )
        fake = FakeDistiller([], judge_error=RuntimeError("llm down"))  # distill -> []
        manager.distiller = fake  # type: ignore[assignment]
        projections = [RunProjection(run_id="s1", trigger_content="整理部署文档")]

        claim_count = await manager._distill_runs(projections)

        assert claim_count == 1
        texts = [c["text"] for c in await manager.claims.get_all_claims()]
        assert any("整理部署文档" in t for t in texts)  # mechanical fallback claim
        old = await manager.claims.get_claim("c-old")
        assert old is not None and old.get("status", "active") == "active"

    async def test_embed_failure_appends_without_judgment(self, tmp_path: Path) -> None:
        class _FailingEmbedder:
            model_name = "fake/embed-4"

            async def embed(self, texts: list[str]) -> list[list[float]]:
                raise RuntimeError("provider down")

        manager = await _seed(
            _manager(tmp_path, _FailingEmbedder()),
            [{"id": "c-old", "text": "旧记忆", "timestamp": "2026-09-10T08:00:00"}],
        )
        fake = FakeDistiller([], verdicts=[])
        manager.distiller = fake  # type: ignore[assignment]

        await manager.remember_fact("新记忆")

        assert fake.judge_calls == []
        assert len(await manager.claims.get_all_claims()) == 2
