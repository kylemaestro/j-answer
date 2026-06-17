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
    embedding_to_bit_blob,
)
from src.search import normalize_tags, row_to_clue_dict, search_clues_by_tags
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
# Candidate pool pulled from the binary (hamming) index before exact float
# reranking. The hamming scan over the whole ~35 MB bit index is cheap; this
# only bounds how many float vectors we then read from clue_embeddings to
# rescore. ~1000 keeps recall high while reading <2 MB of float vectors.
DEFAULT_RERANK_POOL = 1000
RERANK_POOL_CAP = 8000
# When tags narrow a Magic query, the FTS tag match defines the candidate set
# (cosine only ranks within it), so we can afford a larger pool than the hamming
# path. Each 512-d float vector is ~2 KB, so 8000 is a bounded ~16 MB read.
TAG_FILTER_POOL_CAP = 8000

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
    WHERE embedding MATCH vec_bit(?)
      AND k = ?
"""


def magic_rerank_pool() -> int:
    """Candidate count pulled from the hamming index before float reranking."""
    raw = os.environ.get("JANSWER_MAGIC_RERANK_POOL", "").strip()
    if raw:
        try:
            return max(1, min(RERANK_POOL_CAP, int(raw)))
        except ValueError:
            pass
    return DEFAULT_RERANK_POOL


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


def has_embeddings(conn: sqlite3.Connection) -> bool:
    """Cheap existence check (no full COUNT(*) scan of clue_embeddings)."""
    if not embeddings_table_exists(conn):
        return False
    return conn.execute("SELECT 1 FROM clue_embeddings LIMIT 1").fetchone() is not None


# COUNT(*) over the ~1 GB clue_embeddings table is a full scan (~13 s on EBS).
# The row count is static between deploys, so cache it keyed by the DB file's
# (size, mtime) signature. The startup prewarm primes this so no user request
# pays the scan. The magic hot path uses count_vec_index (fast) instead.
_embedded_count_cache: dict[str, tuple[tuple[int, int], int]] = {}


def _db_signature(db_path: str) -> tuple[int, int] | None:
    try:
        st = os.stat(db_path)
    except OSError:
        return None
    return (st.st_size, int(st.st_mtime))


def count_embedded_clues_cached(conn: sqlite3.Connection, db_path: str) -> int:
    """count_embedded_clues with a (size, mtime)-keyed cache to avoid rescans."""
    sig = _db_signature(db_path)
    cached = _embedded_count_cache.get(db_path)
    if cached is not None and sig is not None and cached[0] == sig:
        return cached[1]
    n = count_embedded_clues(conn)
    if sig is not None:
        _embedded_count_cache[db_path] = (sig, n)
    return n


def vec_index_ready(conn: sqlite3.Connection) -> bool:
    return not vec_index_needs_rebuild(conn)


def embeddings_status_details(
    conn: sqlite3.Connection, db_path: str | None = None
) -> dict[str, object]:
    """Diagnostics for /api/embeddings/status."""
    embedded = (
        count_embedded_clues_cached(conn, db_path)
        if db_path
        else count_embedded_clues(conn)
    )
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


def _hamming_candidates(
    conn: sqlite3.Connection,
    query_bit_blob: bytes,
    pool: int,
) -> list[int]:
    """Top-``pool`` clue_ids by hamming distance over the binary index."""
    if pool <= 0:
        return []
    vec_rows = conn.execute(_KNN_VEC_ONLY_SQL, (query_bit_blob, pool)).fetchall()
    return [int(row["clue_id"]) for row in vec_rows]


def _rerank_candidates_by_cosine(
    conn: sqlite3.Connection,
    query_vec: np.ndarray,
    candidate_ids: list[int],
    *,
    result_limit: int,
    min_score: float,
) -> list[tuple[dict[str, Any], float]]:
    """
    Exact cosine rerank of hamming candidates using float vectors.

    Reads the full-precision embeddings for ``candidate_ids`` from
    clue_embeddings (a bounded, by-id read — not a full-table scan), scores
    them against the query, filters by ``min_score`` and returns the top
    ``result_limit`` with cosine-distance for ``_clues_for_picked``.
    """
    if not candidate_ids or result_limit <= 0:
        return []

    placeholders = ",".join("?" * len(candidate_ids))
    rows = conn.execute(
        f"SELECT clue_id, embedding FROM clue_embeddings "
        f"WHERE clue_id IN ({placeholders})",
        candidate_ids,
    ).fetchall()
    if not rows:
        return []

    n = len(rows)
    mat = np.empty((n, EMBEDDING_DIMENSIONS), dtype=np.float32)
    clue_ids = np.empty(n, dtype=np.int64)
    for i, row in enumerate(rows):
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
        zero_blob = embedding_to_bit_blob(
            np.zeros(EMBEDDING_DIMENSIONS, dtype=np.float32)
        )
        t0 = time.perf_counter()
        conn.execute(
            f"SELECT clue_id FROM {VEC_TABLE} WHERE embedding MATCH vec_bit(?) AND k = ?",
            (zero_blob, 1),
        ).fetchall()
        ms = (time.perf_counter() - t0) * 1000.0
        # Prime the embedded-count cache (one slow COUNT(*) here, off the
        # request path) so /api/embeddings/status stays fast for the UI.
        embedded = count_embedded_clues_cached(conn, db_path)
        info.update(
            ok=True,
            reason="warmed",
            indexed=indexed,
            embedded=embedded,
            scan_ms=round(ms, 1),
        )
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


def _tag_filter_candidate_ids(
    conn: sqlite3.Connection, tags: list[str]
) -> list[int] | None:
    """
    Clue IDs matching all ``tags`` (AND) via the FTS tag search, or ``None`` if
    no usable tags were given. An empty list means the tags exclude everything.

    Raises ValueError for malformed field filters (e.g. ``year:`` with no year),
    matching the exact-search contract.
    """
    norm = normalize_tags(tags)
    if not norm:
        return None
    rows = search_clues_by_tags(conn, norm, limit=TAG_FILTER_POOL_CAP)
    return [int(r["id"]) for r in rows]


def search_clues_by_vibe(
    conn: sqlite3.Connection,
    db_path: str,
    query: str,
    *,
    scan_limit: int | None = None,
    min_score: float | None = None,
    tags: list[str] | None = None,
) -> tuple[list[tuple[dict[str, Any], float]], int]:
    """
    Binary-quantized KNN with exact float reranking.

    A hamming-distance scan over the compact ``bit[]`` index (``clue_vec_index``)
    selects a candidate pool, which is then rescored with full-precision cosine
    using the float vectors in ``clue_embeddings``. The bit index is small enough
    (~35 MB) to stay in page cache, so this avoids the ~1 GB float read that
    timed out on small instances.

    When ``tags`` are supplied they act as a hard pre-filter: the FTS tag match
    (same syntax as exact search — ``answer:``, ``clue:``, ``category:``,
    ``year:YYYY``) defines the candidate set, and cosine similarity only ranks
    *within* it. This lets a user say "presidents, but only from category:opera".

    ``scan_limit`` is accepted for API compatibility but no longer bounds I/O
    (the whole bit index is scanned cheaply); the candidate pool size is set by
    ``JANSWER_MAGIC_RERANK_POOL``. Returns (hits, candidates_considered).
    """
    del db_path  # kept for API compatibility
    del scan_limit  # kept for API compatibility; no longer an I/O cap
    if min_score is None:
        min_score = magic_min_score()
    result_limit = magic_result_limit()
    rerank_pool = magic_rerank_pool()

    # Resolve the tag filter up front: a ValueError here (bad field filter)
    # should surface before we spend an OpenAI embed call on the query.
    tag_filter_ids = _tag_filter_candidate_ids(conn, tags or [])
    if tag_filter_ids is not None and not tag_filter_ids:
        return [], 0  # tags matched nothing — no point embedding the query

    if not vec_index_ready(conn):
        # No vec index. Distinguish "no embeddings at all" (empty result) from
        # "embeddings present but index not built" (actionable error) with a
        # cheap existence check — never a full COUNT(*) scan.
        if not has_embeddings(conn):
            return [], 0
        raise RuntimeError(
            "Magic search index is missing or empty. Run: "
            "python scripts/migrate_vec_index.py --db <your-database>"
        )

    indexed = count_vec_index(conn)  # fast: vec0 keeps a small shadow table
    if indexed == 0:
        return [], 0
    pool = min(rerank_pool, indexed)

    load_sqlite_vec(conn)
    t_embed_start = time.perf_counter()
    query_vec = embed_query(query)
    embed_ms = (time.perf_counter() - t_embed_start) * 1000.0

    t_knn_start = time.perf_counter()
    if tag_filter_ids is not None:
        # Tags define the candidate set; cosine ranks within it.
        candidate_ids = tag_filter_ids
        backend = "tag_filter_rerank"
    else:
        query_bit_blob = embedding_to_bit_blob(query_vec)
        candidate_ids = _hamming_candidates(conn, query_bit_blob, pool)
        backend = "bit_hamming_rerank"
    knn_ms = (time.perf_counter() - t_knn_start) * 1000.0

    t_rerank_start = time.perf_counter()
    hits = _rerank_candidates_by_cosine(
        conn,
        query_vec,
        candidate_ids,
        result_limit=result_limit,
        min_score=min_score,
    )
    rerank_ms = (time.perf_counter() - t_rerank_start) * 1000.0
    log.info(
        "magic_search ok query_chars=%s backend=%s "
        "rerank_pool=%s candidates=%s result_limit=%s "
        "embed_ms=%.1f knn_ms=%.1f rerank_ms=%.1f hits=%s",
        len(query.strip()),
        backend,
        pool,
        len(candidate_ids),
        result_limit,
        embed_ms,
        knn_ms,
        rerank_ms,
        len(hits),
    )
    return hits, len(candidate_ids)
