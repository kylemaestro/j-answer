"""Full-text search over clues (clue text, answer, in-game category) via FTS5."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

# User-facing `answer:` / `clue:` / `category:` map to FTS5 column names.
_PREFIX_TO_FTS_COLUMN = {
    "answer": "answer_text",
    "clue": "clue_text",
    "category": "game_category",
}

_TAG_PREFIX_RE = re.compile(
    r"^(?P<kind>answer|clue|category|year)\s*:\s*(?P<rest>.*)$",
    re.IGNORECASE,
)


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


def _quote_fts_phrase(phrase: str) -> str:
    """Single FTS5 phrase token (double-quoted), with quotes escaped."""
    escaped = phrase.replace('"', '""')
    return f'"{escaped}"'


def _classify_tag(tag: str) -> tuple[str, Any]:
    """
    Classify one tag for building the query.

    Returns:
      ("global", phrase) — match phrase in any FTS column (unchanged behavior).
      ("column", (fts_column, phrase)) — match phrase only in that FTS column.
      ("year", yyyy) — filter on clue air_date year (not in FTS).
    """
    m = _TAG_PREFIX_RE.match(tag.strip())
    if not m:
        return ("global", tag)
    kind = m.group("kind").casefold()
    rest = (m.group("rest") or "").strip()
    if not rest:
        raise ValueError(f"Add text or a year after '{kind}:' (empty field filter).")
    if kind == "year":
        if not re.fullmatch(r"\d{4}", rest):
            raise ValueError(
                "Year filters must be exactly four digits, e.g. year:2019 — "
                f"got {rest!r}."
            )
        return ("year", rest)
    col = _PREFIX_TO_FTS_COLUMN[kind]
    return ("column", (col, rest))


def _fts_match_and_years(tags: list[str]) -> tuple[str | None, list[str]]:
    """
    Build FTS MATCH string (or None if only year filters) and a list of year strings
    (AND). Raises ValueError for invalid field filters.
    """
    fts_parts: list[str] = []
    years: list[str] = []
    seen_year: set[str] = set()
    for tag in tags:
        mode, payload = _classify_tag(tag)
        if mode == "global":
            fts_parts.append(_quote_fts_phrase(payload))
        elif mode == "year":
            y = payload
            if y not in seen_year:
                seen_year.add(y)
                years.append(y)
        else:
            col, phrase = payload
            fts_parts.append(f"{col} : {_quote_fts_phrase(phrase)}")
    match_expr = " AND ".join(fts_parts) if fts_parts else None
    return match_expr, years


def search_clues_by_tags(
    conn: sqlite3.Connection,
    tags: list[str],
    *,
    limit: int = 100,
) -> list[sqlite3.Row]:
    """
    Return clue rows matching all tags (AND).

    Tags without a prefix match any FTS column (clue, answer, category), as before.
    Use ``answer:term``, ``clue:term``, or ``category:term`` to restrict a term to
    that field. Use ``year:YYYY`` to restrict to clues from that broadcast year
    (from ``air_date``).
    """
    tags = normalize_tags(tags)
    if not tags:
        return []

    match_expr, years = _fts_match_and_years(tags)
    year_sql = ""
    if years:
        year_sql = " AND " + " AND ".join(
            ["substr(c.air_date, 1, 4) = ?"] * len(years)
        )

    if match_expr is not None:
        cur = conn.execute(
            f"""
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
            WHERE clues_fts MATCH ?{year_sql}
            ORDER BY c.air_date DESC, c.id DESC
            LIMIT ?
            """,
            (match_expr, *years, limit),
        )
    else:
        cur = conn.execute(
            f"""
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
            WHERE 1=1{year_sql}
            ORDER BY c.air_date DESC, c.id DESC
            LIMIT ?
            """,
            (*years, limit),
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
