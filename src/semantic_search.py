"""Semantic (embedding) search via sqlite-vec vec0 KNN over clue_vec_index."""

from __future__ import annotations

import os
import sqlite3
from typing import Any

import numpy as np
import openai

from src.embeddings import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL
from src.search import row_to_clue_dict
from src.vec_index import (
    VEC_INDEX_VERSION,
    VEC_TABLE,
    count_vec_index,
    load_sqlite_vec,
    read_vec_index_version,
    vec_index_needs_rebuild,
    vec_index_table_exists,
)

DEFAULT_MIN_SCORE = 0.45
# Fetch extra neighbors so min_score filtering still returns enough hits.
KNN_OVERSAMPLE = 5
KNN_OVERSAMPLE_CAP = 200

_openai_client: openai.OpenAI | None = None

_CLUE_SELECT = """
    c.id,
    c.jarchive_game_id,
    c.air_date,
    c.round,
    c.game_category,
    c.value_display,
    c.value_amount,
    c.is_daily_double,
    c.clue_text,
    c.answer_text
"""

_KNN_SQL = f"""
    SELECT
        {_CLUE_SELECT},
        v.distance AS vec_distance
    FROM {VEC_TABLE} AS v
    INNER JOIN clues AS c ON c.id = v.clue_id
    WHERE v.embedding MATCH ?
      AND k = ?
"""


def magic_min_score() -> float:
    raw = os.environ.get("JANSWER_MAGIC_MIN_SCORE", "").strip()
    if not raw:
        return DEFAULT_MIN_SCORE
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_MIN_SCORE


def embeddings_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'clue_embeddings'"
    ).fetchone()
    return row is not None


def count_embedded_clues(conn: sqlite3.Connection) -> int:
    if not embeddings_table_exists(conn):
        return 0
    return conn.execute("SELECT COUNT(*) FROM clue_embeddings").fetchone()[0]


def vec_index_ready(conn: sqlite3.Connection) -> bool:
    return not vec_index_needs_rebuild(conn)


def embeddings_status_details(conn: sqlite3.Connection) -> dict[str, object]:
    """Diagnostics for /api/embeddings/status."""
    embedded = count_embedded_clues(conn)
    indexed = count_vec_index(conn) if vec_index_table_exists(conn) else 0
    stored_version = read_vec_index_version(conn)
    needs_rebuild = vec_index_needs_rebuild(conn)
    sqlite_vec_version: str | None = None
    if embedded > 0:
        try:
            sqlite_vec_version = load_sqlite_vec(conn)
        except Exception:
            pass
    return {
        "embedded": embedded,
        "vec_indexed": indexed,
        "vec_index_version": stored_version,
        "vec_index_expected_version": VEC_INDEX_VERSION,
        "needs_vec_rebuild": needs_rebuild,
        "sqlite_vec_version": sqlite_vec_version,
        "search_backend": "vec0_ann" if not needs_rebuild else "none",
        "magic_available": embedded > 0 and not needs_rebuild,
        "min_score": magic_min_score(),
    }


def _distance_to_score(distance: float) -> float:
    """vec0 cosine distance is 1 - similarity for unit vectors."""
    return 1.0 - distance


def _knn_limit(requested: int) -> int:
    return min(max(requested * KNN_OVERSAMPLE, requested), KNN_OVERSAMPLE_CAP)


def _search_vec_knn(
    conn: sqlite3.Connection,
    query_blob: bytes,
    *,
    limit: int,
    min_score: float,
) -> list[tuple[dict[str, Any], float]]:
    if limit <= 0:
        return []

    k = _knn_limit(limit)
    max_distance = 1.0 - min_score
    rows = conn.execute(_KNN_SQL, (query_blob, k)).fetchall()

    hits: list[tuple[dict[str, Any], float]] = []
    for row in rows:
        distance = float(row["vec_distance"])
        if distance > max_distance:
            continue
        score = _distance_to_score(distance)
        hits.append((row_to_clue_dict(row), score))
        if len(hits) >= limit:
            break
    return hits


def _openai_client() -> openai.OpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = openai.OpenAI()
    return _openai_client


def embed_query(text: str) -> np.ndarray:
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is not set on the API server (required for Magic search)."
        )
    q = text.strip()
    if not q:
        raise ValueError("Query must not be empty.")
    client = _openai_client()
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=q,
        dimensions=EMBEDDING_DIMENSIONS,
    )
    vec = np.asarray(response.data[0].embedding, dtype=np.float32)
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec = vec / norm
    return vec


def search_clues_by_vibe(
    conn: sqlite3.Connection,
    db_path: str,
    query: str,
    *,
    limit: int = 100,
    min_score: float | None = None,
) -> list[tuple[dict[str, Any], float]]:
    """
    Cosine similarity over embedded clues via sqlite-vec vec0 KNN.
    Returns (clue dict, score) pairs sorted by score descending.
    """
    del db_path  # kept for API compatibility
    if min_score is None:
        min_score = magic_min_score()

    if count_embedded_clues(conn) == 0:
        return []

    if not vec_index_ready(conn):
        raise RuntimeError(
            "Magic search index is missing or empty. Run: "
            "python scripts/migrate_vec_index.py --db <your-database>"
        )

    load_sqlite_vec(conn)
    query_vec = embed_query(query)
    query_blob = query_vec.astype(np.float32).tobytes()
    return _search_vec_knn(
        conn,
        query_blob,
        limit=limit,
        min_score=min_score,
    )
