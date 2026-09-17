"""sqlite-vec vector index for memory claims (ADR 0017 stage 2).

The index is a pure derived view of ``claims.jsonl``: it can be dropped
and rebuilt from the claim texts at any time, which is exactly what
happens when the configured embedding model changes — the ``vec_meta``
table records the model identity and vector dimension, and a mismatch
against the current config triggers a full rebuild (embedding spaces of
different models are incompatible). All operations are synchronous: the
project is single-process asyncio and the index traffic (a handful of
rows per claim batch, one KNN query per mail) is far too small to block
the event loop; the network-bound embedding calls live in the caller.
"""

import sqlite3
from pathlib import Path

import sqlite_vec

from tlon.base.logger import get_logger

logger = get_logger(__name__)

_META_MODEL = "embedding_model"
_META_DIMENSION = "dimension"


class VectorIndex:
    """claim_id -> embedding index backed by an embedded sqlite-vec database.

    The virtual table is created lazily at the first rebuild/upsert that
    knows the vector dimension; an index without a table is simply empty.
    Cosine distance (declared on the column) keeps the similarity math
    independent of provider vector normalization.
    """

    def __init__(self, db_path: Path):
        """Open (or create) the index database.

        Args:
            db_path: Database file, conventionally
                ``{agent_home}/memory/claims_vec.db``
        """
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.enable_load_extension(True)
        sqlite_vec.load(self._conn)
        self._conn.enable_load_extension(False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS vec_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def _table_exists(self) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'claims_vec'"
        ).fetchone()
        return row is not None

    def model_meta(self) -> tuple[str, int] | None:
        """The indexed embedding model and dimension; None when unbuilt."""
        rows = dict(self._conn.execute("SELECT key, value FROM vec_meta").fetchall())
        model = rows.get(_META_MODEL)
        dimension = rows.get(_META_DIMENSION)
        if model is None or dimension is None:
            return None
        return model, int(dimension)

    def rebuild(self, model: str, entries: list[tuple[str, list[float]]]) -> None:
        """Drop and rebuild the whole index for ``model`` from ``entries``.

        With no entries the index resets to unbuilt (no vec table, no
        meta) — an empty memory has nothing to index.
        """
        self._conn.execute("DROP TABLE IF EXISTS claims_vec")
        self._conn.execute("DELETE FROM vec_meta")
        if not entries:
            self._conn.commit()
            return
        dimension = len(entries[0][1])
        self._conn.execute(
            f"CREATE VIRTUAL TABLE claims_vec USING vec0("
            f"claim_id TEXT PRIMARY KEY, embedding float[{dimension}] distance_metric=cosine)"
        )
        self._conn.executemany(
            "INSERT INTO claims_vec (claim_id, embedding) VALUES (?, ?)",
            [
                (claim_id, sqlite_vec.serialize_float32(vector))
                for claim_id, vector in entries
                if len(vector) == dimension
            ],
        )
        self._conn.execute(
            "INSERT INTO vec_meta (key, value) VALUES (?, ?), (?, ?)",
            (_META_MODEL, model, _META_DIMENSION, str(dimension)),
        )
        self._conn.commit()

    def upsert(self, claim_id: str, vector: list[float]) -> None:
        """Insert or replace one claim's embedding.

        A dimension mismatch against the built index is logged and
        skipped (never raised): the claim stays reachable via the
        recency fallback, and the next startup rebuild reconciles.
        """
        if not self._table_exists():
            logger.warning("vector_index_upsert_unbuilt", claim_id=claim_id)
            return
        meta = self.model_meta()
        if meta is not None and len(vector) != meta[1]:
            logger.warning(
                "vector_index_dimension_mismatch",
                claim_id=claim_id,
                expected=meta[1],
                actual=len(vector),
            )
            return
        # vec0 virtual tables reject INSERT OR REPLACE — delete then insert.
        self._conn.execute("DELETE FROM claims_vec WHERE claim_id = ?", (claim_id,))
        self._conn.execute(
            "INSERT INTO claims_vec (claim_id, embedding) VALUES (?, ?)",
            (claim_id, sqlite_vec.serialize_float32(vector)),
        )
        self._conn.commit()

    def delete(self, claim_id: str) -> None:
        """Remove one claim's embedding (a missing row is not an error)."""
        if not self._table_exists():
            return
        self._conn.execute("DELETE FROM claims_vec WHERE claim_id = ?", (claim_id,))
        self._conn.commit()

    def search(self, vector: list[float], limit: int) -> list[tuple[str, float]]:
        """KNN search: up to ``limit`` ``(claim_id, similarity)`` pairs.

        Similarity is ``1 - cosine_distance`` clamped to [0, 1], best
        first. An unbuilt index searches as empty.
        """
        if not self._table_exists():
            return []
        rows = self._conn.execute(
            "SELECT claim_id, distance FROM claims_vec "
            "WHERE embedding MATCH ? ORDER BY distance LIMIT ?",
            (sqlite_vec.serialize_float32(vector), limit),
        ).fetchall()
        return [(str(claim_id), max(0.0, 1.0 - float(distance))) for claim_id, distance in rows]

    def indexed_ids(self) -> set[str]:
        """All claim ids currently in the index (the backfill diff set)."""
        if not self._table_exists():
            return set()
        rows = self._conn.execute("SELECT claim_id FROM claims_vec").fetchall()
        return {str(row[0]) for row in rows}
