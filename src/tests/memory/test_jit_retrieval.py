"""JIT memory retrieval (ADR 0017 stage 2).

Pins the composite ranking (α·similarity + β·recency + γ·importance),
the degradation ladder (no query / no embedder / embedding failure all
fall back to recency order), and the index lifecycle at the manager
level (rebuild on embedding-model change, backfill of unindexed claims,
write-path indexing). All embedding goes through an injected fake — no
network.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tlon.memory.manager import MemoryManager
from tlon.memory.models import MemoryConfig

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


class FailingEmbedder:
    """Every call fails — the provider-outage shape."""

    model_name = "fake/embed-4"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("provider down")


def _manager(tmp_path: Path, embedder: Any | None = None, max_claims: int = 10) -> MemoryManager:
    config = MemoryConfig()
    config.context_max_claims = max_claims
    return MemoryManager("test-agent", config, data_dir=tmp_path, embedder=embedder)


def _claim(
    claim_id: str,
    text: str,
    *,
    age_days: float = 0,
    importance: float = 0.5,
) -> dict[str, Any]:
    timestamp = (datetime.now(UTC) - timedelta(days=age_days)).isoformat()
    return {
        "id": claim_id,
        "text": text,
        "timestamp": timestamp,
        "status": "active",
        "importance": importance,
    }


async def _load_with_claims(manager: MemoryManager, claims: list[dict[str, Any]]) -> MemoryManager:
    """Seed claims (bypassing the index write path), then load — the
    load-time index build is what puts them into the index."""
    for claim in claims:
        await manager.claims.add_claim(claim)
    await manager.load()
    return manager


# ===== JIT ranking =====


async def test_jit_relevant_old_claim_beats_irrelevant_fresh_one(
    tmp_path: Path,
) -> None:
    """Similarity dominates the composite score: an old claim aligned
    with the query outranks a fresh, more important but orthogonal one
    — relevance to the current mail is the point of JIT ()."""
    query = "部署脚本在哪里？"
    relevant = "部署脚本在 scripts/deploy.sh"
    noise = "用户喜欢手冲咖啡"
    embedder = FakeEmbedder({query: _axis(0), relevant: _axis(0), noise: _axis(1)})
    manager = await _load_with_claims(
        _manager(tmp_path, embedder, max_claims=1),
        [
            _claim("c-relevant", relevant, age_days=30, importance=0.3),
            _claim("c-noise", noise, age_days=0, importance=0.9),
        ],
    )

    blocks = await manager.build_context_blocks(query)

    assert len(blocks) == 1
    assert relevant in blocks[0].content
    assert noise not in blocks[0].content
    assert blocks[0].source_ids == ["c-relevant"]


async def test_jit_importance_breaks_similarity_tie(tmp_path: Path) -> None:
    """Same vector, same age: importance is the tiebreaker, and the
    block order follows the composite score."""
    query = "项目的关键决策"
    weak = "项目用 pytest 跑测试"
    strong = "项目拍板不引入图数据库"
    embedder = FakeEmbedder({query: _axis(0), weak: _axis(0), strong: _axis(0)})
    manager = await _load_with_claims(
        _manager(tmp_path, embedder, max_claims=2),
        [
            _claim("c-weak", weak, importance=0.3),
            _claim("c-strong", strong, importance=0.9),
        ],
    )

    blocks = await manager.build_context_blocks(query)

    lines = blocks[0].content.splitlines()
    assert len(lines) == 2
    assert strong in lines[0]
    assert weak in lines[1]


async def test_config_weights_change_jit_ranking(tmp_path: Path) -> None:
    """The composite score weights are live config: cranking
    retrieval.gamma_importance lets a maximally important but orthogonal
    claim outrank the query-aligned one."""
    query = "部署脚本在哪里？"
    relevant = "部署脚本在 scripts/deploy.sh"
    important = "生产环境禁止直接改库"
    embedder = FakeEmbedder({query: _axis(0), relevant: _axis(0), important: _axis(1)})
    manager = _manager(tmp_path, embedder, max_claims=1)
    manager.config.retrieval_gamma_importance = 3.0
    manager = await _load_with_claims(
        manager,
        [
            _claim("c-relevant", relevant, importance=0.0),
            _claim("c-important", important, importance=1.0),
        ],
    )

    blocks = await manager.build_context_blocks(query)

    assert blocks[0].source_ids == ["c-important"]


# ===== Degradation ladder =====


async def test_no_query_falls_back_to_recency(tmp_path: Path) -> None:
    embedder = FakeEmbedder({})
    manager = await _load_with_claims(
        _manager(tmp_path, embedder, max_claims=1),
        [_claim("c-old", "旧的记忆", age_days=10), _claim("c-new", "新的记忆")],
    )

    blocks = await manager.build_context_blocks(None)

    assert "新的记忆" in blocks[0].content
    assert "旧的记忆" not in blocks[0].content


async def test_no_embedder_falls_back_to_recency(tmp_path: Path) -> None:
    """No embedding model configured: vector retrieval simply does not
    exist — the member keeps working with recency-ordered memory."""
    manager = await _load_with_claims(
        _manager(tmp_path, None, max_claims=1),
        [_claim("c-old", "旧的记忆", age_days=10), _claim("c-new", "新的记忆")],
    )

    blocks = await manager.build_context_blocks("旧")

    assert "新的记忆" in blocks[0].content


async def test_embed_failure_falls_back_to_recency(tmp_path: Path) -> None:
    """A provider outage must never break the session: the index fails
    to build, retrieval degrades, claims are still injected by recency."""
    manager = await _load_with_claims(
        _manager(tmp_path, FailingEmbedder(), max_claims=1),
        [_claim("c-old", "旧的记忆", age_days=10), _claim("c-new", "新的记忆")],
    )

    blocks = await manager.build_context_blocks("任意问题")

    assert "新的记忆" in blocks[0].content


# ===== Index lifecycle =====


async def test_model_change_triggers_rebuild(tmp_path: Path) -> None:
    """Embedding spaces are incompatible across models: a configured
    model change rebuilds the whole index from the claim texts."""
    text = "用户偏好 Python"
    manager_a = await _load_with_claims(
        _manager(tmp_path, FakeEmbedder({text: _axis(0)}, model_name="fake/model-a")),
        [_claim("c1", text)],
    )
    assert manager_a._index is not None
    assert manager_a._index.model_meta() == ("fake/model-a", _DIM)

    manager_b = await _load_with_claims(
        _manager(tmp_path, FakeEmbedder({text: _axis(1)}, model_name="fake/model-b")),
        [],
    )
    await manager_b.load()

    assert manager_b._index is not None
    assert manager_b._index.model_meta() == ("fake/model-b", _DIM)
    assert manager_b._index.indexed_ids() == {"c1"}


async def test_load_backfills_unindexed_claims(tmp_path: Path) -> None:
    """Claims written while the index was down get their vectors at the
    next startup — nothing is permanently invisible to retrieval."""
    text = "回读索引之外的记忆"
    manager = await _load_with_claims(
        _manager(tmp_path, FakeEmbedder({text: _axis(0)})),
        [_claim("c1", text)],
    )
    assert manager._index is not None
    manager._index.delete("c1")  # simulate an index/store drift

    manager2 = _manager(tmp_path, FakeEmbedder({text: _axis(0)}))
    await manager2.load()

    assert manager2._index is not None
    assert manager2._index.indexed_ids() == {"c1"}


async def test_remember_fact_indexes_immediately(tmp_path: Path) -> None:
    """The write path embeds inline: a just-recorded claim is retrievable
    in the very next mail ('s '记了看不到' stays dead)."""
    text = "刚记住的事实"
    manager = await _load_with_claims(
        _manager(tmp_path, FakeEmbedder({text: _axis(0), "问": _axis(0)})),
        [],
    )

    claim_id = await manager.remember_fact(text)

    assert manager._index is not None
    assert claim_id in manager._index.indexed_ids()


async def test_delete_claim_removes_vector(tmp_path: Path) -> None:
    text = "将被遗忘的事"
    manager = await _load_with_claims(
        _manager(tmp_path, FakeEmbedder({text: _axis(0)})),
        [_claim("c1", text)],
    )
    assert manager._index is not None

    assert await manager.delete_claim("c1") is True
    assert manager._index.indexed_ids() == set()


# ===== searchMemories =====


async def test_search_memories_vector_path_exposes_source_sessions(
    tmp_path: Path,
) -> None:
    """Vector hits carry their provenance: date plus source session ids,
    so the agent can cite where a memory came from."""
    text = "部署脚本在 scripts/deploy.sh"
    query = "怎么部署"
    embedder = FakeEmbedder({query: _axis(0), text: _axis(0)})
    manager = await _load_with_claims(
        _manager(tmp_path, embedder),
        [{**_claim("c1", text), "source_session_ids": ["sess-1", "sess-2"]}],
    )

    result = await manager.search_memories(query)

    assert text in result
    assert "来源 session: sess-1, sess-2" in result


async def test_search_memories_without_index_keeps_keyword_fallback(
    tmp_path: Path,
) -> None:
    manager = await _load_with_claims(
        _manager(tmp_path, None),
        [_claim("c1", "用户偏好 Python")],
    )

    result = await manager.search_memories("Python")

    assert "用户偏好 Python" in result
