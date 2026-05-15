"""sqlite-vec vec0 index for fast Magic (semantic) search."""

from __future__ import annotations

import sqlite3
from typing import Iterable

import numpy as np
import sqlite_vec

from src.embeddings import EMBEDDING_BYTES, EMBEDDING_DIMENSIONS, blob_to_embedding

VEC_TABLE = "clue_vec_index"
META_VEC_INDEX_VERSION = "vec_index_version"
VEC_INDEX_VERSION = "2"

_VEC0_DDL = f"""
CREATE VIRTUAL TABLE {VEC_TABLE} USING vec0(
    clue_id INTEGER PRIMARY KEY,
    embedding FLOAT[{EMBEDDING_DIMENSIONS}] distance_metric=cosine
    indexed by flat()
);
"""

_DELETE_VEC_TRIGGER = f"""
CREATE TRIGGER IF NOT EXISTS clue_embeddings_ad_vec
AFTER DELETE ON clue_embeddings
BEGIN
    DELETE FROM {VEC_TABLE} WHERE clue_id = OLD.clue_id;
END;
"""


def load_sqlite_vec(conn: sqlite3.Connection) -> str:
    """Load the sqlite-vec extension. Returns vec_version()."""
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return conn.execute("SELECT vec_version()").fetchone()[0]


def read_vec_index_version(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?",
        (META_VEC_INDEX_VERSION,),
    ).fetchone()
    return row[0] if row else None


def vec_index_needs_rebuild(conn: sqlite3.Connection) -> bool:
    """True when the vec table is missing, empty, or built with an older schema."""
    if not vec_index_table_exists(conn) or count_vec_index(conn) == 0:
        return True
    stored = read_vec_index_version(conn)
    return stored != VEC_INDEX_VERSION


def vec_index_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (VEC_TABLE,),
    ).fetchone()
    return row is not None


def count_vec_index(conn: sqlite3.Connection) -> int:
    if not vec_index_table_exists(conn):
        return 0
    return conn.execute(f"SELECT COUNT(*) FROM {VEC_TABLE}").fetchone()[0]


def normalize_embedding_blob(blob: bytes) -> bytes:
    """L2-normalize a stored embedding BLOB for cosine distance in vec0."""
    vec = blob_to_embedding(blob)
    norm = float(np.linalg.norm(vec))
    if norm > 0:
        vec = vec / norm
    return vec.astype(np.float32).tobytes()


def _ensure_meta(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )


def ensure_vec_index(conn: sqlite3.Connection) -> None:
    """Create the vec0 ANN table and delete-sync trigger if missing."""
    _ensure_meta(conn)
    if vec_index_table_exists(conn):
        if not vec_index_needs_rebuild(conn):
            conn.execute(_DELETE_VEC_TRIGGER)
            return
        drop_vec_index(conn)
    conn.execute(_VEC0_DDL)
    conn.execute(_DELETE_VEC_TRIGGER)
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        (META_VEC_INDEX_VERSION, VEC_INDEX_VERSION),
    )
    conn.commit()


def drop_vec_index(conn: sqlite3.Connection) -> None:
    if not vec_index_table_exists(conn):
        return
    conn.execute(f"DROP TABLE {VEC_TABLE}")
    conn.execute("DROP TRIGGER IF EXISTS clue_embeddings_ad_vec")
    conn.commit()


def upsert_vec_rows(
    conn: sqlite3.Connection,
    rows: Iterable[tuple[int, bytes]],
) -> int:
    """Insert or replace vectors in the vec0 index. Returns rows written."""
    ensure_vec_index(conn)
    n = 0
    for clue_id, blob in rows:
        if len(blob) != EMBEDDING_BYTES:
            raise ValueError(
                f"clue_id {clue_id}: expected {EMBEDDING_BYTES} bytes, got {len(blob)}"
            )
        norm_blob = normalize_embedding_blob(blob)
        conn.execute(
            f"DELETE FROM {VEC_TABLE} WHERE clue_id = ?",
            (clue_id,),
        )
        conn.execute(
            f"INSERT INTO {VEC_TABLE} (clue_id, embedding) VALUES (?, ?)",
            (clue_id, norm_blob),
        )
        n += 1
    if n:
        conn.commit()
    return n


def rebuild_vec_index_from_embeddings(
    conn: sqlite3.Connection,
    *,
    batch_size: int = 5000,
) -> tuple[int, int]:
    """
    Rebuild clue_vec_index from clue_embeddings.
    Returns (source_row_count, rows_written).
    """
    load_sqlite_vec(conn)
    source_count = conn.execute("SELECT COUNT(*) FROM clue_embeddings").fetchone()[0]
    drop_vec_index(conn)
    ensure_vec_index(conn)
    if source_count == 0:
        return 0, 0

    cur = conn.execute(
        "SELECT clue_id, embedding FROM clue_embeddings ORDER BY clue_id"
    )
    written = 0
    while True:
        batch = cur.fetchmany(batch_size)
        if not batch:
            break
        upsert_vec_rows(conn, ((r[0], r[1]) for r in batch))
        written += len(batch)
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        (META_VEC_INDEX_VERSION, VEC_INDEX_VERSION),
    )
    conn.commit()
    return source_count, written
