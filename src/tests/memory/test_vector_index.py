"""VectorIndex — the sqlite-vec derived view of the claim store (ADR 0017
stage 2).

Pure sqlite, no network: pins CRUD, cosine similarity ranking, the
model/dimension metadata that gates rebuilds, and the defensive paths
(search/upsert on an unbuilt index, dimension mismatch on upsert).
"""

from pathlib import Path

from tlon.memory.vector_index import VectorIndex

MODEL = "fake/embed-4"


def _unit(dim: int, index: int) -> list[float]:
    """A unit vector along one axis — distances are exact and obvious."""
    return [1.0 if i == index else 0.0 for i in range(dim)]


def _index(tmp_path: Path) -> VectorIndex:
    return VectorIndex(tmp_path / "memory" / "claims_vec.db")


def test_unbuilt_index_is_empty(tmp_path: Path) -> None:
    index = _index(tmp_path)

    assert index.model_meta() is None
    assert index.indexed_ids() == set()
    assert index.search(_unit(4, 0), 5) == []
    index.delete("anything")  # no table yet — must not raise
    index.close()


def test_rebuild_then_search_ranks_by_similarity(tmp_path: Path) -> None:
    index = _index(tmp_path)
    index.rebuild(
        MODEL,
        [("near", _unit(4, 0)), ("far", _unit(4, 1)), ("mid", [0.7, 0.7, 0.0, 0.0])],
    )

    assert index.model_meta() == (MODEL, 4)
    assert index.indexed_ids() == {"near", "far", "mid"}

    hits = index.search(_unit(4, 0), 3)
    assert [claim_id for claim_id, _ in hits] == ["near", "mid", "far"]
    similarity = dict(hits)
    assert similarity["near"] == 1.0  # identical vector, zero distance
    assert similarity["far"] == 0.0  # orthogonal, clamped at zero
    assert 0.0 < similarity["mid"] < 1.0
    index.close()


def test_upsert_replaces_and_delete_removes(tmp_path: Path) -> None:
    index = _index(tmp_path)
    index.rebuild(MODEL, [("a", _unit(4, 0))])

    index.upsert("b", _unit(4, 1))
    assert index.indexed_ids() == {"a", "b"}

    index.upsert("a", _unit(4, 1))  # replace: a now sits on b's axis
    assert index.search(_unit(4, 1), 1)[0][0] in {"a", "b"}

    index.delete("b")
    assert index.indexed_ids() == {"a"}
    index.delete("b")  # deleting a missing row is not an error
    index.close()


def test_upsert_dimension_mismatch_is_skipped(tmp_path: Path) -> None:
    """A wrong-sized vector never corrupts the built index — the claim
    stays reachable via the recency fallback until the next rebuild."""
    index = _index(tmp_path)
    index.rebuild(MODEL, [("a", _unit(4, 0))])

    index.upsert("b", _unit(8, 0))

    assert index.indexed_ids() == {"a"}
    index.close()


def test_rebuild_replaces_previous_model(tmp_path: Path) -> None:
    """Model change = incompatible embedding space: the rebuild swaps the
    whole table and rewrites the metadata."""
    index = _index(tmp_path)
    index.rebuild(MODEL, [("a", _unit(4, 0))])

    index.rebuild("other/embed-8", [("a", _unit(8, 2)), ("b", _unit(8, 3))])

    assert index.model_meta() == ("other/embed-8", 8)
    assert index.indexed_ids() == {"a", "b"}
    assert index.search(_unit(8, 3), 1)[0][0] == "b"
    index.close()


def test_rebuild_with_no_entries_resets_to_unbuilt(tmp_path: Path) -> None:
    """An empty memory has nothing to index — the index resets fully."""
    index = _index(tmp_path)
    index.rebuild(MODEL, [("a", _unit(4, 0))])

    index.rebuild(MODEL, [])

    assert index.model_meta() is None
    assert index.indexed_ids() == set()
    assert index.search(_unit(4, 0), 5) == []
    index.close()


def test_index_persists_across_connections(tmp_path: Path) -> None:
    """The db file survives reopening — the metadata gate works across
    process restarts."""
    index = _index(tmp_path)
    index.rebuild(MODEL, [("a", _unit(4, 0))])
    index.close()

    reopened = _index(tmp_path)
    assert reopened.model_meta() == (MODEL, 4)
    assert reopened.indexed_ids() == {"a"}
    reopened.close()
