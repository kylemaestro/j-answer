"""SQLite schema, FTS5 index, and clue persistence."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

SCHEMA_VERSION = 2

DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clues (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  jarchive_clue_id INTEGER NOT NULL UNIQUE,
  jarchive_game_id INTEGER NOT NULL,
  show_number INTEGER,
  air_date TEXT NOT NULL,
  round TEXT NOT NULL,
  game_category TEXT NOT NULL,
  value_display TEXT,
  value_amount INTEGER,
  is_daily_double INTEGER NOT NULL DEFAULT 0,
  clue_text TEXT NOT NULL,
  answer_text TEXT NOT NULL,
  ai_category TEXT,
  ai_subcategory TEXT,
  scraped_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_clues_air_date ON clues(air_date);
CREATE INDEX IF NOT EXISTS idx_clues_game_id ON clues(jarchive_game_id);
CREATE INDEX IF NOT EXISTS idx_clues_round ON clues(round);
CREATE INDEX IF NOT EXISTS idx_clues_ai_category ON clues(ai_category);

CREATE VIRTUAL TABLE IF NOT EXISTS clues_fts USING fts5(
  clue_text,
  answer_text,
  game_category,
  tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS clues_ai AFTER INSERT ON clues BEGIN
  INSERT INTO clues_fts(rowid, clue_text, answer_text, game_category)
  VALUES (new.id, new.clue_text, new.answer_text, new.game_category);
END;

CREATE TRIGGER IF NOT EXISTS clues_au AFTER UPDATE ON clues BEGIN
  INSERT INTO clues_fts(clues_fts, rowid) VALUES('delete', old.id);
  INSERT INTO clues_fts(rowid, clue_text, answer_text, game_category)
  VALUES (new.id, new.clue_text, new.answer_text, new.game_category);
END;

CREATE TRIGGER IF NOT EXISTS clues_ad AFTER DELETE ON clues BEGIN
  INSERT INTO clues_fts(clues_fts, rowid) VALUES('delete', old.id);
END;
"""

CRAWL_DDL = """
CREATE TABLE IF NOT EXISTS crawl_games (
  game_id INTEGER PRIMARY KEY,
  season_key TEXT NOT NULL,
  status TEXT NOT NULL,
  error TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  clues_inserted INTEGER,
  clues_skipped INTEGER,
  last_duration_ms INTEGER,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_crawl_games_status ON crawl_games(status);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    v = int(row[0]) if row else 0
    if v < 2:
        conn.executescript(CRAWL_DDL)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


def insert_clues(
    conn: sqlite3.Connection,
    rows: Iterable[Mapping[str, Any]],
) -> tuple[int, int]:
    """Insert clues; skips duplicates on jarchive_clue_id. Returns (inserted, skipped)."""
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    skipped = 0
    cur = conn.cursor()
    for row in rows:
        payload = dict(row)
        payload.setdefault("scraped_at", now)
        try:
            cur.execute(
                """
                INSERT INTO clues (
                  jarchive_clue_id, jarchive_game_id, show_number, air_date, round,
                  game_category, value_display, value_amount, is_daily_double,
                  clue_text, answer_text, ai_category, ai_subcategory, scraped_at
                ) VALUES (
                  :jarchive_clue_id, :jarchive_game_id, :show_number, :air_date, :round,
                  :game_category, :value_display, :value_amount, :is_daily_double,
                  :clue_text, :answer_text, :ai_category, :ai_subcategory, :scraped_at
                )
                """,
                payload,
            )
            inserted += 1
        except sqlite3.IntegrityError:
            skipped += 1
    conn.commit()
    return inserted, skipped
