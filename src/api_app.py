"""HTTP API for the flashcard UI and search."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from src.db import connect
from src.search import normalize_tags, row_to_clue_dict, search_clues_by_tags
from src.semantic_search import (
    count_embedded_clues,
    embeddings_status_details,
    search_clues_by_vibe,
)

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


app = FastAPI(title="j-answer API", version="0.1.0")
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
        return embeddings_status_details(conn)
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
    limit: int = Query(100, ge=1, le=500),
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
        embedded_pool = count_embedded_clues(conn)
        if embedded_pool == 0:
            return {
                "mode": "magic",
                "query": query,
                "embedded_pool": 0,
                "min_score": min_score if min_score is not None else magic_min_score(),
                "count": 0,
                "clues": [],
            }
        try:
            hits = search_clues_by_vibe(
                conn,
                path,
                query,
                limit=limit,
                min_score=min_score,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e)) from e
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
        "min_score": min_score if min_score is not None else magic_min_score(),
        "count": len(hits),
        "clues": [{**clue, "score": score} for clue, score in hits],
    }
