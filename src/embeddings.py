"""
Semantic embedding storage for clues (`clue_embeddings` table).

Format contract (must match `embed/vector-embed.py` and any search code):
  - Model: text-embedding-3-small (OpenAI)
  - Dimensions: 512 (requested at embed time; not the model default 1536)
  - Stored type: numpy float32, little-endian
  - On disk: raw bytes in `embedding` BLOB (no header, length = 512 * 4 = 2048)
  - Text embedded per clue: "{game_category} | {clue_text} | {answer_text}"
  - Query vectors must use the same model and dimensions for cosine similarity.
"""

from __future__ import annotations

import sqlite3
import struct

import numpy as np

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMENSIONS = 512
EMBEDDING_BYTES = EMBEDDING_DIMENSIONS * struct.calcsize("<f")
# Binary-quantized form: one sign bit per dimension, packed MSB-first.
# 512 dims -> 64 bytes (vs 2048 for float32). Used by clue_vec_index (bit[])
# for a fast hamming-distance candidate scan; float blobs above are kept for
# exact cosine reranking of the candidates.
EMBEDDING_BIT_BYTES = EMBEDDING_DIMENSIONS // 8


def embedding_to_bit_blob(vector: list[float] | np.ndarray) -> bytes:
    """
    Binary-quantize an embedding to a packed bit BLOB for sqlite-vec ``bit[]``.

    Each dimension contributes one bit: 1 where the component is > 0, else 0,
    packed MSB-first via ``np.packbits``. The exact same packing must be used
    for both indexed clue vectors and query vectors so hamming distances line
    up. Returns ``EMBEDDING_BIT_BYTES`` bytes.
    """
    arr = np.asarray(vector, dtype=np.float32)
    if arr.shape != (EMBEDDING_DIMENSIONS,):
        raise ValueError(
            f"expected shape ({EMBEDDING_DIMENSIONS},), got {arr.shape}"
        )
    bits = (arr > 0).astype(np.uint8)
    return np.packbits(bits).tobytes()

CLUE_EMBEDDINGS_DDL = """
CREATE TABLE clue_embeddings (
    clue_id INTEGER PRIMARY KEY,
    embedding BLOB NOT NULL,
    FOREIGN KEY (clue_id) REFERENCES clues(id) ON DELETE CASCADE
);
"""


def embedding_to_blob(vector: list[float] | np.ndarray) -> bytes:
    """Serialize one embedding to the on-disk BLOB format."""
    arr = np.asarray(vector, dtype=np.float32)
    if arr.shape != (EMBEDDING_DIMENSIONS,):
        raise ValueError(
            f"expected shape ({EMBEDDING_DIMENSIONS},), got {arr.shape}"
        )
    return arr.tobytes()


def blob_to_embedding(data: bytes) -> np.ndarray:
    """Deserialize one BLOB to a float32 vector of shape (EMBEDDING_DIMENSIONS,)."""
    if len(data) != EMBEDDING_BYTES:
        raise ValueError(
            f"expected {EMBEDDING_BYTES} bytes, got {len(data)}"
        )
    return np.frombuffer(data, dtype=np.float32).copy()


def _embeddings_table_has_fk(conn: sqlite3.Connection) -> bool:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'clue_embeddings'"
    ).fetchone():
        return False
    fks = conn.execute("PRAGMA foreign_key_list(clue_embeddings)").fetchall()
    return any(fk[2] == "clues" and fk[3] == "id" for fk in fks)


def ensure_embeddings_schema(conn: sqlite3.Connection) -> None:
    """
    Create `clue_embeddings` or migrate an older table without FK / CASCADE.
  """
    conn.execute("PRAGMA foreign_keys = ON")
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'clue_embeddings'"
    ).fetchone():
        conn.executescript(CLUE_EMBEDDINGS_DDL)
        conn.commit()
        return

    if _embeddings_table_has_fk(conn):
        return

    conn.executescript(
        """
        CREATE TABLE clue_embeddings_new (
            clue_id INTEGER PRIMARY KEY,
            embedding BLOB NOT NULL,
            FOREIGN KEY (clue_id) REFERENCES clues(id) ON DELETE CASCADE
        );
        INSERT INTO clue_embeddings_new (clue_id, embedding)
        SELECT clue_id, embedding FROM clue_embeddings;
        DROP TABLE clue_embeddings;
        ALTER TABLE clue_embeddings_new RENAME TO clue_embeddings;
        """
    )
    conn.commit()


def count_embeddings(conn: sqlite3.Connection) -> tuple[int, int, int]:
    """Return (clue_total, embedded_count, pending_count)."""
    clue_total = conn.execute("SELECT COUNT(*) FROM clues").fetchone()[0]
    embedded = conn.execute("SELECT COUNT(*) FROM clue_embeddings").fetchone()[0]
    pending = conn.execute(
        """
        SELECT COUNT(*)
        FROM clues c
        WHERE NOT EXISTS (
            SELECT 1 FROM clue_embeddings e WHERE e.clue_id = c.id
        )
        """
    ).fetchone()[0]
    return clue_total, embedded, pending
