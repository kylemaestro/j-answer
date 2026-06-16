"""HTTP API for the flashcard UI and search."""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAIError

from src.db import connect
from src.search import normalize_tags, row_to_clue_dict, search_clues_by_tags
from src.semantic_search import (
    embeddings_status_details,
    has_embeddings,
    magic_min_score,
    magic_scan_limit_default,
    search_clues_by_vibe,
    warm_magic_index,
)
from src.vec_index import count_vec_index

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB = _REPO_ROOT / "j-answer.db"


def _db_path() -> str:
    return os.environ.get("JANSWER_DB", str(_DEFAULT_DB))


def _cors_origins() -> List[str]:
    """Local dev defaults plus optional CORS_ORIGINS (comma-separated) for production."""
    base = [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ]
    extra = os.environ.get("CORS_ORIGINS", "").strip()
    if not extra:
        return base
    for part in extra.split(","):
        o = part.strip()
        if o and o not in base:
            base.append(o)
    return base


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Pre-warm the sqlite-vec flat index in a background thread on startup.

    On t4g.small (2 GB RAM) the ~1 GB index lives comfortably in OS page cache
    once it has been touched once, but the first read after deploy/reboot still
    has to pull it off EBS — that's the ~10 s cold-start that would otherwise
    hit the first user. Running the warm in a thread keeps /api/health
    responsive immediately; Uvicorn doesn't wait for the scan to finish.
    """
    db_path = _db_path()

    def _warm() -> None:
        result = warm_magic_index(db_path)
        if result.get("ok"):
            log.info(
                "magic_prewarm ok indexed=%s scan_ms=%s",
                result.get("indexed"),
                result.get("scan_ms"),
            )
        else:
            log.info("magic_prewarm skipped reason=%s", result.get("reason"))

    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _warm)
    except Exception as exc:
        log.warning("magic_prewarm dispatch failed: %s", exc)

    yield


app = FastAPI(title="j-answer API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/random-clue")
def random_clue() -> dict:
    path = _db_path()
    if not Path(path).is_file():
        raise HTTPException(
            status_code=503,
            detail=f"Database file not found at {path!r}. Run the scraper first or set JANSWER_DB.",
        )
    conn = connect(path)
    try:
        row = conn.execute(
            """
            SELECT
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
            FROM clues
            ORDER BY RANDOM()
            LIMIT 1
            """
        ).fetchone()
    except sqlite3.OperationalError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Database error: {e}. Ensure the schema exists (run the CLI once).",
        ) from e
    finally:
        conn.close()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="No clues in the database yet. Scrape some games first.",
        )

    return row_to_clue_dict(row)


@app.get("/api/search")
def search_clues(
    tag: Optional[List[str]] = Query(
        None,
        description=(
            "Repeat `tag=` for each term (AND). Default: match clue, answer, or category. "
            "Prefix to narrow: `answer:`, `clue:`, `category:`, or `year:` (four digits, e.g. year:2019)."
        ),
    ),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    """
    Full-text search using SQLite FTS5 (`clues_fts`) plus optional year filter on `air_date`.

    Field filters: ``answer:mallard`` (answer only), ``clue:...``, ``category:...``,
    ``year:2020`` (broadcast year). Unprefixed tags still match any indexed column.
    Multiple `tag` query params are combined with AND.
    """
    path = _db_path()
    if not Path(path).is_file():
        raise HTTPException(
            status_code=503,
            detail=f"Database file not found at {path!r}. Run the scraper first or set JANSWER_DB.",
        )

    raw_tags = tag or []
    norm = normalize_tags(raw_tags)
    if not norm:
        return {"tags": [], "count": 0, "clues": []}

    conn = connect(path)
    try:
        rows = search_clues_by_tags(conn, norm, limit=limit)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "fts" in msg or "malformed" in msg or "syntax" in msg:
            raise HTTPException(
                status_code=400,
                detail=f"Search could not be parsed: {e}",
            ) from e
        raise HTTPException(
            status_code=503,
            detail=f"Database error: {e}",
        ) from e
    finally:
        conn.close()

    return {
        "mode": "exact",
        "tags": norm,
        "count": len(rows),
        "clues": [row_to_clue_dict(r) for r in rows],
    }


@app.get("/api/embeddings/status")
def embeddings_status() -> dict:
    """How many clues have vectors (Magic search pool size)."""
    path = _db_path()
    if not Path(path).is_file():
        raise HTTPException(
            status_code=503,
            detail=f"Database file not found at {path!r}. Run the scraper first or set JANSWER_DB.",
        )
    conn = connect(path)
    try:
        return embeddings_status_details(conn, db_path=path)
    except sqlite3.OperationalError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Database error: {e}",
        ) from e
    finally:
        conn.close()


@app.get("/api/search/magic")
def search_clues_magic(
    q: str = Query(..., min_length=1, description="Natural-language vibe query"),
    limit: int = Query(
        magic_scan_limit_default(),
        ge=1,
        le=20_000_000,
        description=(
            "Max embedding rows to scan (I/O cap). Results are still capped at "
            "JANSWER_MAGIC_RESULT_LIMIT or 100. Use a lower value on small instances; "
            "set to embedded_pool size (or higher) for a full-index vec0 scan."
        ),
    ),
    min_score: Optional[float] = Query(
        None,
        ge=0.0,
        le=1.0,
        description="Minimum cosine similarity (default from JANSWER_MAGIC_MIN_SCORE or 0.45)",
    ),
) -> dict:
    """
    Semantic search over clues present in ``clue_embeddings`` only.
    Requires ``OPENAI_API_KEY`` on the server to embed the query.

    ``limit`` bounds how many vec-index rows are read, not how many clues are returned.
    """
    path = _db_path()
    if not Path(path).is_file():
        raise HTTPException(
            status_code=503,
            detail=f"Database file not found at {path!r}. Run the scraper first or set JANSWER_DB.",
        )

    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Query must not be empty.")

    conn = connect(path)
    try:
        # Fast: vec0 keeps a small shadow table, so this is ~ms (a COUNT(*) over
        # the ~1 GB clue_embeddings table would be ~13 s). After migration the
        # vec index is 1:1 with clue_embeddings, so this is the searchable pool.
        embedded_pool = count_vec_index(conn)
        if embedded_pool == 0 and not has_embeddings(conn):
            return {
                "mode": "magic",
                "query": query,
                "embedded_pool": 0,
                "scan_limit": limit,
                "rows_scanned": 0,
                "min_score": min_score if min_score is not None else magic_min_score(),
                "count": 0,
                "clues": [],
            }
        try:
            hits, rows_scanned = search_clues_by_vibe(
                conn,
                path,
                query,
                scan_limit=limit,
                min_score=min_score,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
        except OpenAIError as e:
            log.warning("Magic search OpenAI error: %s", e, exc_info=True)
            raise HTTPException(
                status_code=503,
                detail=f"Embedding provider error: {e}",
            ) from e
        except Exception as e:
            log.exception("Magic search failed")
            raise HTTPException(
                status_code=500,
                detail="Magic search failed (see server logs).",
            ) from e
    except sqlite3.OperationalError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Database error: {e}",
        ) from e
    finally:
        conn.close()

    return {
        "mode": "magic",
        "query": query,
        "embedded_pool": embedded_pool,
        "scan_limit": limit,
        "rows_scanned": rows_scanned,
        "min_score": min_score if min_score is not None else magic_min_score(),
        "count": len(hits),
        "clues": [{**clue, "score": score} for clue, score in hits],
    }
