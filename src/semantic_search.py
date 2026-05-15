"""Semantic (embedding) search over clues that have rows in clue_embeddings."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import openai

from src.embeddings import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    blob_to_embedding,
)
from src.search import row_to_clue_dict

DEFAULT_MIN_SCORE = 0.32
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

# db_path -> (mtime, clue_ids, normalized matrix, row dicts by id)
_matrix_cache: dict[str, tuple[float, np.ndarray, np.ndarray, dict[int, sqlite3.Row]]] = {}


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


def _normalize_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.maximum(norms, 1e-8)


def _load_matrix(
    conn: sqlite3.Connection, db_path: str
) -> tuple[np.ndarray, np.ndarray, dict[int, sqlite3.Row]]:
    path = Path(db_path)
    mtime = path.stat().st_mtime
    cached = _matrix_cache.get(db_path)
    if cached is not None and cached[0] == mtime:
        return cached[1], cached[2], cached[3]

    cur = conn.execute(
        f"""
        SELECT {_CLUE_SELECT}, e.embedding AS embedding_blob
        FROM clues AS c
        INNER JOIN clue_embeddings AS e ON e.clue_id = c.id
        ORDER BY c.id
        """
    )
    rows = cur.fetchall()
    if not rows:
        empty_ids = np.array([], dtype=np.int64)
        empty_matrix = np.empty((0, EMBEDDING_DIMENSIONS), dtype=np.float32)
        _matrix_cache[db_path] = (mtime, empty_ids, empty_matrix, {})
        return empty_ids, empty_matrix, {}

    clue_ids: list[int] = []
    vectors: list[np.ndarray] = []
    by_id: dict[int, sqlite3.Row] = {}
    for row in rows:
        clue_ids.append(row["id"])
        vectors.append(blob_to_embedding(row["embedding_blob"]))
        by_id[row["id"]] = row

    ids_arr = np.array(clue_ids, dtype=np.int64)
    matrix = np.stack(vectors, axis=0)
    matrix = _normalize_rows(matrix)
    _matrix_cache[db_path] = (mtime, ids_arr, matrix, by_id)
    return ids_arr, matrix, by_id


def embed_query(text: str) -> np.ndarray:
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is not set on the API server (required for Magic search)."
        )
    q = text.strip()
    if not q:
        raise ValueError("Query must not be empty.")
    client = openai.OpenAI()
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
    Cosine similarity over embedded clues only.
    Returns (clue dict, score) pairs sorted by score descending.
    """
    if min_score is None:
        min_score = magic_min_score()

    ids, matrix, by_id = _load_matrix(conn, db_path)
    if matrix.shape[0] == 0:
        return []

    query_vec = embed_query(query)
    scores = matrix @ query_vec
    above = np.where(scores >= min_score)[0]
    if above.size == 0:
        return []

    order = above[np.argsort(scores[above])[::-1]]
    if limit > 0:
        order = order[:limit]

    out: list[tuple[dict[str, Any], float]] = []
    for idx in order:
        clue_id = int(ids[idx])
        row = by_id[clue_id]
        out.append((row_to_clue_dict(row), float(scores[idx])))
    return out
