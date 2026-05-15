#!/usr/bin/env python3
"""Build or rebuild the sqlite-vec ANN index from clue_embeddings."""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.embeddings import count_embeddings  # noqa: E402
from src.vec_index import (  # noqa: E402
    count_vec_index,
    load_sqlite_vec,
    rebuild_vec_index_from_embeddings,
    vec_index_table_exists,
)

log = logging.getLogger(__name__)
DEFAULT_DB = REPO_ROOT / "j-answer.db"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate clue_embeddings BLOBs into sqlite-vec vec0 index."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"SQLite database path (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5000,
        help="Rows per upsert batch (default: 5000)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    db_path = args.db.resolve()
    if not db_path.is_file():
        log.error("Database file not found: %s", db_path)
        return 1
    if args.batch_size < 1:
        log.error("--batch-size must be at least 1")
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        load_sqlite_vec(conn)
    except Exception as exc:
        log.error("Failed to load sqlite-vec: %s", exc)
        log.error("Install with: pip install sqlite-vec")
        conn.close()
        return 1

    _, embedded, _ = count_embeddings(conn)
    had_index = vec_index_table_exists(conn)
    before = count_vec_index(conn) if had_index else 0

    log.info(
        "Database: %s | clue_embeddings=%s | vec_index before=%s (exists=%s)",
        db_path,
        embedded,
        before,
        had_index,
    )

    if embedded == 0:
        log.warning("No rows in clue_embeddings — nothing to index.")
        conn.close()
        return 0

    log.info("Rebuilding %s from clue_embeddings...", "clue_vec_index")
    t0 = time.perf_counter()
    source, written = rebuild_vec_index_from_embeddings(
        conn, batch_size=args.batch_size
    )
    elapsed = time.perf_counter() - t0
    after = count_vec_index(conn)
    conn.close()

    log.info(
        "Done in %.1fs: source=%s written=%s vec_index=%s",
        elapsed,
        source,
        written,
        after,
    )
    if after != embedded:
        log.warning(
            "vec_index count (%s) != clue_embeddings (%s) — investigate.",
            after,
            embedded,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
