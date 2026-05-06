"""Full-text search over clues (clue text, answer, in-game category) via FTS5."""

from __future__ import annotations

import sqlite3
from typing import Any


def normalize_tags(raw: list[str]) -> list[str]:
    """Trim, drop empties, preserve first-seen casing; dedupe case-insensitively."""
    seen_set: set[str] = set()
    out: list[str] = []
    for t in raw:
        s = t.strip()
        if not s:
            continue
        key = s.casefold()
        if key in seen_set:
            continue
        seen_set.add(key)
        out.append(s)
    return out


def fts_and_match_expression(tags: list[str]) -> str:
    """
    Build an FTS5 MATCH string: each tag is a phrase (quoted); combined with AND.
    All terms must match somewhere in the indexed row (clue_text, answer_text, game_category).
    """
    parts: list[str] = []
    for tag in tags:
        escaped = tag.replace('"', '""')
        parts.append(f'"{escaped}"')
    return " AND ".join(parts)


def search_clues_by_tags(
    conn: sqlite3.Connection,
    tags: list[str],
    *,
    limit: int = 100,
) -> list[sqlite3.Row]:
    """
    Return clue rows matching all tags (AND) against FTS5 clue/answer/category columns.
    """
    tags = normalize_tags(tags)
    if not tags:
        return []

    match_expr = fts_and_match_expression(tags)
    cur = conn.execute(
        """
        SELECT DISTINCT
          c.id,
          c.jarchive_game_id,
          c.air_date,
          c.round,
          c.game_category,
          c.value_display,
          c.value_amount,
          c.is_daily_double,
          c.clue_text,
          c.answer_text
        FROM clues AS c
        INNER JOIN clues_fts ON clues_fts.rowid = c.id
        WHERE clues_fts MATCH ?
        ORDER BY c.air_date DESC, c.id DESC
        LIMIT ?
        """,
        (match_expr, limit),
    )
    return cur.fetchall()


def row_to_clue_dict(row: sqlite3.Row) -> dict[str, Any]:
    """API-shaped dict from a clues SELECT row."""
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
