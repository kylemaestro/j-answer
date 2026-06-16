"""Semantic (embedding) search via sqlite-vec vec0 KNN over clue_vec_index."""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from typing import Any

import numpy as np
import openai

from src.embeddings import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    blob_to_embedding,
)
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
# Max clues returned to the client (independent of how many rows we scan).
DEFAULT_RESULT_LIMIT = 100
# Default embedding rows to read per Magic query (override via API ?limit= or env).
DEFAULT_SCAN_LIMIT = 50_000
# Fetch extra neighbors so min_score filtering still returns enough hits (full vec0 path).
KNN_OVERSAMPLE = 5
KNN_OVERSAMPLE_CAP = 200

_openai_singleton: openai.OpenAI | None = None

log = logging.getLogger(__name__)

_CLUE_COLUMNS = """
    id,
    jarchive_game_id,
    air_date,
    round,
    game_category,
    value_display,
    value_amount,
    is_daily_double,
    clue_text,
    answer_text
"""

_KNN_VEC_ONLY_SQL = f"""
    SELECT clue_id, distance
    FROM {VEC_TABLE}
    WHERE embedding MATCH ?
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


def magic_scan_limit_default() -> int:
    """Default ?limit= for Magic search (max embedding rows to scan)."""
    raw = os.environ.get("JANSWER_MAGIC_SCAN_LIMIT", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return DEFAULT_SCAN_LIMIT


def magic_result_limit() -> int:
    raw = os.environ.get("JANSWER_MAGIC_RESULT_LIMIT", "").strip()
    if raw:
        try:
            return max(1, min(500, int(raw)))
        except ValueError:
            pass
    return DEFAULT_RESULT_LIMIT


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
    sqlite_vec_version: str | None = None
    sqlite_vec_error: str | None = None
    indexed = 0
    needs_rebuild = True
    stored_version: str | None = None

    if vec_index_table_exists(conn):
        try:
            sqlite_vec_version = load_sqlite_vec(conn)
            indexed = count_vec_index(conn)
            stored_version = read_vec_index_version(conn)
            needs_rebuild = vec_index_needs_rebuild(conn)
        except Exception as exc:
            sqlite_vec_error = str(exc)
    elif embedded > 0:
        needs_rebuild = True

    return {
        "embedded": embedded,
        "vec_indexed": indexed,
        "vec_index_version": stored_version,
        "vec_index_expected_version": VEC_INDEX_VERSION,
        "needs_vec_rebuild": needs_rebuild,
        "sqlite_vec_version": sqlite_vec_version,
        "sqlite_vec_error": sqlite_vec_error,
        "search_backend": "vec0_ann" if not needs_rebuild else "none",
        "magic_available": embedded > 0 and not needs_rebuild,
        "min_score": magic_min_score(),
    }


def _distance_to_score(distance: float) -> float:
    """vec0 cosine distance is 1 - similarity for unit vectors."""
    return 1.0 - distance


def _knn_limit(result_limit: int) -> int:
    return min(
        max(result_limit * KNN_OVERSAMPLE, result_limit),
        KNN_OVERSAMPLE_CAP,
    )


def _clues_for_picked(
    conn: sqlite3.Connection,
    picked: list[tuple[int, float]],
) -> list[tuple[dict[str, Any], float]]:
    if not picked:
        return []

    clue_ids = [cid for cid, _ in picked]
    placeholders = ",".join("?" * len(clue_ids))
    clue_sql = f"""
        SELECT {_CLUE_COLUMNS}
        FROM clues
        WHERE id IN ({placeholders})
    """
    clue_rows = conn.execute(clue_sql, clue_ids).fetchall()
    by_id = {int(r["id"]): r for r in clue_rows}

    hits: list[tuple[dict[str, Any], float]] = []
    for cid, distance in picked:
        row = by_id.get(cid)
        if row is None:
            continue
        score = _distance_to_score(distance)
        hits.append((row_to_clue_dict(row), score))
    return hits


def _search_vec_knn_full(
    conn: sqlite3.Connection,
    query_blob: bytes,
    *,
    result_limit: int,
    min_score: float,
) -> list[tuple[dict[str, Any], float]]:
    """
    Full-index vec0 MATCH (flat index scans every row regardless of k).

    Two-phase KNN: vec0 MATCH only, then clues lookup by id.
    JOIN inside the MATCH query has been observed to hang on some linux/arm +
    sqlite-vec builds at large scale; splitting keeps plans simple.
    """
    if result_limit <= 0:
        return []

    k = _knn_limit(result_limit)
    max_distance = 1.0 - min_score
    vec_rows = conn.execute(_KNN_VEC_ONLY_SQL, (query_blob, k)).fetchall()

    picked: list[tuple[int, float]] = []
    for row in vec_rows:
        distance = float(row["distance"])
        if distance > max_distance:
            continue
        picked.append((int(row["clue_id"]), distance))
        if len(picked) >= result_limit:
            break

    return _clues_for_picked(conn, picked)


def _search_vec_scan_subset(
    conn: sqlite3.Connection,
    query_vec: np.ndarray,
    *,
    scan_limit: int,
    result_limit: int,
    min_score: float,
) -> list[tuple[dict[str, Any], float]]:
    """
    Bounded I/O: read at most ``scan_limit`` embeddings, score in numpy.

    Uses stable ORDER BY clue_id so repeated queries hit the same slice.
    """
    if scan_limit <= 0 or result_limit <= 0:
        return []

    vec_rows = conn.execute(
        f"""
        SELECT clue_id, embedding
        FROM {VEC_TABLE}
        ORDER BY clue_id
        LIMIT ?
        """,
        (scan_limit,),
    ).fetchall()
    if not vec_rows:
        return []

    n = len(vec_rows)
    mat = np.empty((n, EMBEDDING_DIMENSIONS), dtype=np.float32)
    clue_ids = np.empty(n, dtype=np.int64)
    for i, row in enumerate(vec_rows):
        clue_ids[i] = int(row["clue_id"])
        mat[i] = blob_to_embedding(row["embedding"])

    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.where(norms > 0, norms, 1.0)
    mat /= norms

    q = query_vec.astype(np.float32, copy=False)
    scores = mat @ q
    above = scores >= min_score
    if not np.any(above):
        return []

    idx = np.flatnonzero(above)
    cand_scores = scores[idx]
    cand_ids = clue_ids[idx]
    if len(idx) > result_limit:
        top = np.argpartition(cand_scores, -result_limit)[-result_limit:]
        order = top[np.argsort(cand_scores[top])[::-1]]
    else:
        order = np.argsort(cand_scores)[::-1]

    picked = [
        (int(cand_ids[i]), 1.0 - float(cand_scores[i])) for i in order
    ]
    return _clues_for_picked(conn, picked)


def warm_magic_index(db_path: str) -> dict[str, object]:
    """
    Touch every page of the sqlite-vec flat index to pull it into OS page cache.

    Issues a dummy KNN against a zero vector (k=1) — flat indexes scan all rows
    regardless of k, so this forces the working set into memory without making
    any OpenAI calls. Called once at API startup (see ``src/api_app.py`` lifespan)
    so the first real Magic search after a deploy doesn't pay the ~1 GB EBS read.

    Returns a small diagnostic dict for logging. Never raises — pre-warm is
    best-effort; a missing DB or vec index should not block the API from starting.
    """
    info: dict[str, object] = {"ok": False, "reason": "unknown"}
    if not os.path.isfile(db_path):
        info["reason"] = "db_missing"
        return info
    from src.db import connect

    conn = None
    try:
        conn = connect(db_path)
        if not vec_index_table_exists(conn):
            info["reason"] = "vec_index_missing"
            return info
        load_sqlite_vec(conn)
        indexed = count_vec_index(conn)
        if indexed == 0:
            info["reason"] = "vec_index_empty"
            info["indexed"] = 0
            return info
        zero_blob = np.zeros(EMBEDDING_DIMENSIONS, dtype=np.float32).tobytes()
        t0 = time.perf_counter()
        conn.execute(
            f"SELECT clue_id FROM {VEC_TABLE} WHERE embedding MATCH ? AND k = ?",
            (zero_blob, 1),
        ).fetchall()
        ms = (time.perf_counter() - t0) * 1000.0
        info.update(ok=True, reason="warmed", indexed=indexed, scan_ms=round(ms, 1))
        return info
    except Exception as exc:
        info["reason"] = f"error: {exc!r}"
        return info
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def get_openai_client() -> openai.OpenAI:
    global _openai_singleton
    if _openai_singleton is None:
        timeout_s = 45.0
        raw_t = os.environ.get("JANSWER_OPENAI_TIMEOUT_SEC", "").strip()
        if raw_t:
            try:
                timeout_s = max(5.0, float(raw_t))
            except ValueError:
                pass
        retries = 2
        raw_r = os.environ.get("JANSWER_OPENAI_MAX_RETRIES", "").strip()
        if raw_r:
            try:
                retries = max(0, min(10, int(raw_r)))
            except ValueError:
                pass
        _openai_singleton = openai.OpenAI(timeout=timeout_s, max_retries=retries)
    return _openai_singleton


def embed_query(text: str) -> np.ndarray:
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError(
            "OPENAI_API_KEY is not set on the API server (required for Magic search)."
        )
    q = text.strip()
    if not q:
        raise ValueError("Query must not be empty.")
    client = get_openai_client()
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
    scan_limit: int | None = None,
    min_score: float | None = None,
) -> tuple[list[tuple[dict[str, Any], float]], int]:
    """
    Cosine similarity over embedded clues.

    ``scan_limit`` caps how many rows are read from ``clue_vec_index`` (I/O bound
    on small instances). When ``scan_limit`` is at least the vec index size, uses
    sqlite-vec vec0 KNN over the full flat index. Returns (hits, rows_scanned).
    """
    del db_path  # kept for API compatibility
    if min_score is None:
        min_score = magic_min_score()
    if scan_limit is None:
        scan_limit = magic_scan_limit_default()
    result_limit = magic_result_limit()

    if count_embedded_clues(conn) == 0:
        return [], 0

    if not vec_index_ready(conn):
        raise RuntimeError(
            "Magic search index is missing or empty. Run: "
            "python scripts/migrate_vec_index.py --db <your-database>"
        )

    indexed = count_vec_index(conn)
    rows_to_scan = min(scan_limit, indexed) if indexed else 0
    use_full_index = scan_limit >= indexed and indexed > 0

    load_sqlite_vec(conn)
    t_embed_start = time.perf_counter()
    query_vec = embed_query(query)
    embed_ms = (time.perf_counter() - t_embed_start) * 1000.0
    query_blob = query_vec.astype(np.float32).tobytes()
    t_knn_start = time.perf_counter()
    if use_full_index:
        hits = _search_vec_knn_full(
            conn,
            query_blob,
            result_limit=result_limit,
            min_score=min_score,
        )
        backend = "vec0_full"
    else:
        hits = _search_vec_scan_subset(
            conn,
            query_vec,
            scan_limit=rows_to_scan,
            result_limit=result_limit,
            min_score=min_score,
        )
        backend = "subset_scan"
    knn_ms = (time.perf_counter() - t_knn_start) * 1000.0
    log.info(
        "magic_search ok query_chars=%s scan_limit=%s rows_scanned=%s "
        "backend=%s result_limit=%s embed_ms=%.1f knn_ms=%.1f hits=%s",
        len(query.strip()),
        scan_limit,
        rows_to_scan,
        backend,
        result_limit,
        embed_ms,
        knn_ms,
        len(hits),
    )
    return hits, rows_to_scan
