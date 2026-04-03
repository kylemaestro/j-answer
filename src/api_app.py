"""Minimal HTTP API for the flashcard UI (Phase 2)."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from src.db import connect

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB = _REPO_ROOT / "j-answer.db"


def _db_path() -> str:
    return os.environ.get("JANSWER_DB", str(_DEFAULT_DB))


app = FastAPI(title="j-answer API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
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

    air = row["air_date"]
    year = None
    if air and len(air) >= 4 and air[:4].isdigit():
        year = int(air[:4])

    return {
        "id": row["id"],
        "jarchive_game_id": row["jarchive_game_id"],
        "air_date": air,
        "year": year,
        "round": row["round"],
        "game_category": row["game_category"],
        "value_display": row["value_display"],
        "value_amount": row["value_amount"],
        "is_daily_double": bool(row["is_daily_double"]),
        "clue_text": row["clue_text"],
        "answer_text": row["answer_text"],
    }
