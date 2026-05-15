"""Semantic (embedding) search over clues that have rows in clue_embeddings."""

from __future__ import annotations

import heapq
import os
import sqlite3
from typing import Any

import numpy as np
import openai

from src.embeddings import (
    EMBEDDING_BYTES,
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
)
from src.search import row_to_clue_dict

DEFAULT_MIN_SCORE = 0.45
DEFAULT_BATCH_SIZE = 20_000

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

_EMBEDDED_CLUES_SQL = f"""
    SELECT {_CLUE_SELECT}, e.embedding AS embedding_blob
    FROM clues AS c
    INNER JOIN clue_embeddings AS e ON e.clue_id = c.id
    ORDER BY c.id
"""


def magic_min_score() -> float:
    raw = os.environ.get("JANSWER_MAGIC_MIN_SCORE", "").strip()
    if not raw:
        return DEFAULT_MIN_SCORE
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_MIN_SCORE


def magic_batch_size() -> int:
    raw = os.environ.get("JANSWER_MAGIC_BATCH_SIZE", "").strip()
    if not raw:
        return DEFAULT_BATCH_SIZE
    try:
        n = int(raw)
        return max(1, n)
    except ValueError:
        return DEFAULT_BATCH_SIZE


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


def _update_topk(
    heap: list[tuple[float, int, sqlite3.Row]],
    score: float,
    clue_id: int,
    row: sqlite3.Row,
    *,
    limit: int,
    min_score: float,
) -> None:
    if score < min_score:
        return
    entry = (score, clue_id, row)
    if len(heap) < limit:
        heapq.heappush(heap, entry)
    elif score > heap[0][0]:
        heapq.heapreplace(heap, entry)


def _search_chunked(
    conn: sqlite3.Connection,
    query_vec: np.ndarray,
    *,
    limit: int,
    min_score: float,
    batch_size: int,
) -> list[tuple[dict[str, Any], float]]:
    """Brute-force cosine similarity in batches (bounded RAM)."""
    if limit <= 0:
        return []

    heap: list[tuple[float, int, sqlite3.Row]] = []
    cur = conn.execute(_EMBEDDED_CLUES_SQL)

    while True:
        rows = cur.fetchmany(batch_size)
        if not rows:
            break

        n = len(rows)
        ids = np.empty(n, dtype=np.int64)
        matrix = np.empty((n, EMBEDDING_DIMENSIONS), dtype=np.float32)
        for i, row in enumerate(rows):
            ids[i] = row["id"]
            blob = row["embedding_blob"]
            if len(blob) != EMBEDDING_BYTES:
                raise ValueError(
                    f"clue_id {row['id']}: expected {EMBEDDING_BYTES} embedding bytes, "
                    f"got {len(blob)}"
                )
            matrix[i] = np.frombuffer(blob, dtype=np.float32)

        matrix = _normalize_rows(matrix)
        scores = matrix @ query_vec

        for i in range(n):
            s = float(scores[i])
            _update_topk(
                heap,
                s,
                int(ids[i]),
                rows[i],
                limit=limit,
                min_score=min_score,
            )

    if not heap:
        return []

    heap.sort(key=lambda x: x[0], reverse=True)
    return [(row_to_clue_dict(row), score) for score, _, row in heap]


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
    Cosine similarity over embedded clues only (chunked scan, bounded memory).
    Returns (clue dict, score) pairs sorted by score descending.
    """
    del db_path  # kept for API compatibility; scan uses conn only
    if min_score is None:
        min_score = magic_min_score()

    if count_embedded_clues(conn) == 0:
        return []

    query_vec = embed_query(query)
    return _search_chunked(
        conn,
        query_vec,
        limit=limit,
        min_score=min_score,
        batch_size=magic_batch_size(),
    )
