"""Batch-embed clues into clue_embeddings (separate from the clues table)."""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

import openai
from openai import APIConnectionError, APITimeoutError, RateLimitError

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.embeddings import (  # noqa: E402
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    count_embeddings,
    embedding_to_blob,
    ensure_embeddings_schema,
)

log = logging.getLogger(__name__)

DEFAULT_DB = REPO_ROOT / "j-answer.db"
BATCH_SIZE = 1000  # OpenAI allows up to 2048 per batch
DEFAULT_RETRIES = 5
DEFAULT_BACKOFF_BASE = 2.0

_RETRYABLE_EXCEPTIONS = (
    RateLimitError,
    APIConnectionError,
    APITimeoutError,
)


def get_embed_text(row: sqlite3.Row) -> str:
    category = row["game_category"] or ""
    clue = row["clue_text"] or ""
    answer = row["answer_text"] or ""
    return f"{category} | {clue} | {answer}"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, _RETRYABLE_EXCEPTIONS):
        return True
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code in (429, 500, 502, 503)
    return False


def create_embeddings_with_retry(
    client: openai.OpenAI,
    texts: list[str],
    *,
    retries: int,
    backoff_base_s: float,
) -> list[list[float]]:
    n = max(1, retries)
    last_exc: BaseException | None = None
    for attempt in range(n):
        try:
            response = client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=texts,
                dimensions=EMBEDDING_DIMENSIONS,
            )
            return [e.embedding for e in response.data]
        except Exception as exc:
            last_exc = exc
            if not _is_retryable(exc) or attempt >= n - 1:
                raise
            wait = backoff_base_s * (2**attempt)
            log.warning(
                "OpenAI embeddings failed (%s); retry %s/%s in %.1fs",
                exc,
                attempt + 2,
                n,
                wait,
            )
            time.sleep(wait)
    raise last_exc  # pragma: no cover


def run_embedding_job(
    db_path: Path,
    batch_size: int,
    *,
    limit: int,
    retries: int,
    backoff_base_s: float,
) -> int:
    log.info(
        "Database: %s (exists=%s, size=%s bytes)",
        db_path,
        db_path.is_file(),
        db_path.stat().st_size if db_path.is_file() else 0,
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    if not _table_exists(conn, "clues"):
        log.error(
            "Table 'clues' not found. Point --db at your scraped database "
            "(default: %s).",
            DEFAULT_DB,
        )
        conn.close()
        return 1

    ensure_embeddings_schema(conn)

    clue_total, embedded_before, pending = count_embeddings(conn)
    to_process = pending if limit <= 0 else min(limit, pending)
    log.info(
        "clues=%s | already embedded=%s | pending=%s | will process=%s",
        clue_total,
        embedded_before,
        pending,
        to_process,
    )
    log.info(
        "Embeddings: model=%s dims=%s; table clue_embeddings (FK → clues, ON DELETE CASCADE).",
        EMBEDDING_MODEL,
        EMBEDDING_DIMENSIONS,
    )

    if clue_total == 0:
        log.warning("No rows in clues — wrong database file or empty scrape.")
        conn.close()
        return 1

    if pending == 0:
        log.info("Nothing to do; every clue already has an embedding.")
        conn.close()
        return 0

    if to_process == 0:
        log.info("Nothing to do (--limit or pending count is zero).")
        conn.close()
        return 0

    if not os.environ.get("OPENAI_API_KEY"):
        log.error("OPENAI_API_KEY is not set. Set it in your shell, then re-run.")
        conn.close()
        return 1

    sql = """
        SELECT c.id, c.game_category, c.clue_text, c.answer_text
        FROM clues c
        WHERE NOT EXISTS (
            SELECT 1 FROM clue_embeddings e WHERE e.clue_id = c.id
        )
        ORDER BY c.id
    """
    if limit > 0:
        sql += " LIMIT ?"
        cursor = conn.execute(sql, (limit,))
    else:
        cursor = conn.execute(sql)

    client = openai.OpenAI()
    total_this_run = 0
    batch_num = 0
    while True:
        if limit > 0 and total_this_run >= limit:
            break
        fetch_n = batch_size
        if limit > 0:
            fetch_n = min(fetch_n, limit - total_this_run)
        rows = cursor.fetchmany(fetch_n)
        if not rows:
            break

        batch_num += 1
        ids = [r["id"] for r in rows]
        id_range = f"{ids[0]}..{ids[-1]}" if len(ids) > 1 else str(ids[0])
        texts = [get_embed_text(r) for r in rows]
        log.info(
            "Batch %s: embedding %s clues (ids %s)...",
            batch_num,
            len(rows),
            id_range,
        )

        embeddings = create_embeddings_with_retry(
            client,
            texts,
            retries=retries,
            backoff_base_s=backoff_base_s,
        )

        conn.executemany(
            "INSERT OR IGNORE INTO clue_embeddings (clue_id, embedding) VALUES (?, ?)",
            [
                (clue_id, embedding_to_blob(emb))
                for clue_id, emb in zip(ids, embeddings)
            ],
        )
        conn.commit()

        total_this_run += len(rows)
        pending_left = max(0, pending - total_this_run)
        log.info(
            "Batch %s done: +%s this run (%s total) | embedded≈%s | pending≈%s",
            batch_num,
            len(rows),
            total_this_run,
            embedded_before + total_this_run,
            pending_left,
        )

        time.sleep(0.1)

    _, embedded_final, pending_final = count_embeddings(conn)
    log.info(
        "Finished. Embedded %s clues this run; clue_embeddings=%s; pending=%s.",
        total_this_run,
        embedded_final,
        pending_final,
    )
    conn.close()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Embed clues into clue_embeddings via OpenAI."
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
        default=BATCH_SIZE,
        help=f"Clues per API request (default: {BATCH_SIZE})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max clues to embed this run (0 = all pending)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help=f"API attempts per batch on retryable errors (default: {DEFAULT_RETRIES})",
    )
    parser.add_argument(
        "--backoff-base",
        type=float,
        default=DEFAULT_BACKOFF_BASE,
        help=(
            f"Seconds before first retry; doubles each attempt "
            f"(default: {DEFAULT_BACKOFF_BASE})"
        ),
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
        log.error("Pass --db /path/to/j-answer.db (or your scrape DB).")
        sys.exit(1)

    if args.batch_size < 1:
        log.error("--batch-size must be at least 1")
        sys.exit(1)
    if args.limit < 0:
        log.error("--limit must be >= 0")
        sys.exit(1)
    if args.retries < 1:
        log.error("--retries must be at least 1")
        sys.exit(1)
    if args.backoff_base < 0:
        log.error("--backoff-base must be >= 0")
        sys.exit(1)

    sys.exit(
        run_embedding_job(
            db_path,
            args.batch_size,
            limit=args.limit,
            retries=args.retries,
            backoff_base_s=args.backoff_base,
        )
    )


if __name__ == "__main__":
    main()
