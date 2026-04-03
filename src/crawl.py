"""Resumable full-archive crawl with per-game checkpoints and ETA."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

import requests
import sqlite3

from janswer.db import insert_clues
from janswer.parser import parse_game_html
from janswer.scraper import (
    fetch_game_html,
    fetch_listseasons_html,
    fetch_season_html,
    parse_game_ids_from_season_page,
    parse_season_keys_from_list,
    polite_delay,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def crawl_counts(conn: sqlite3.Connection) -> dict[str, int]:
    total = conn.execute("SELECT COUNT(*) FROM crawl_games").fetchone()[0]
    pending = conn.execute(
        "SELECT COUNT(*) FROM crawl_games WHERE status = 'pending'"
    ).fetchone()[0]
    failed = conn.execute(
        "SELECT COUNT(*) FROM crawl_games WHERE status = 'failed'"
    ).fetchone()[0]
    complete = conn.execute(
        "SELECT COUNT(*) FROM crawl_games WHERE status = 'complete'"
    ).fetchone()[0]
    remaining = pending + failed
    return {
        "total": total,
        "pending": pending,
        "failed": failed,
        "complete": complete,
        "remaining": remaining,
    }


def discover(
    conn: sqlite3.Connection,
    delay_s: float,
    *,
    max_seasons: int = 0,
    log: Callable[[str], None] = print,
) -> tuple[int, int]:
    """
    Fetch listseasons + each season page; register game_ids in crawl_games (INSERT OR IGNORE).
    Does not overwrite completed or failed rows. Returns (season_pages_fetched, games_added).
    """
    now = _utc_now()
    list_html = fetch_listseasons_html()
    season_keys = parse_season_keys_from_list(list_html)
    if max_seasons > 0:
        season_keys = season_keys[:max_seasons]
    log(f"discover: {len(season_keys)} season keys from listseasons.php")
    games_added = 0
    for i, sk in enumerate(season_keys):
        if i > 0:
            polite_delay(delay_s)
        try:
            sh = fetch_season_html(sk)
        except requests.RequestException as e:
            log(f"discover: season={sk!r} FAILED: {e}")
            continue
        gids = parse_game_ids_from_season_page(sh)
        before = conn.execute("SELECT COUNT(*) FROM crawl_games").fetchone()[0]
        for gid in gids:
            conn.execute(
                """
                INSERT OR IGNORE INTO crawl_games
                  (game_id, season_key, status, error, attempts, updated_at)
                VALUES (?, ?, 'pending', NULL, 0, ?)
                """,
                (gid, sk, now),
            )
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM crawl_games").fetchone()[0]
        delta = after - before
        games_added += delta
        log(f"discover: season={sk!r} games_on_page={len(gids)} new_in_db={delta}")
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('crawl_discovery_at', ?)",
        (now,),
    )
    conn.commit()
    return len(season_keys), games_added


def _fmt_duration(seconds: float) -> str:
    if seconds < 0 or seconds != seconds:  # NaN
        return "?"
    sec = int(seconds)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m {sec % 60}s"
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    if h >= 48:
        d, h = divmod(h, 24)
        return f"{d}d {h}h {m}m"
    return f"{h}h {m}m {s}s"


def _scrape_one(
    conn: sqlite3.Connection,
    game_id: int,
    *,
    http_retries: int,
    backoff_base_s: float,
) -> tuple[str, str | None, int, int, int]:
    """
    Returns (status, error_or_none, clues_inserted, clues_skipped, duration_ms).
    status is 'complete' or 'failed'.
    """
    t0 = time.perf_counter()
    html: str | None = None
    n = max(1, http_retries)
    for attempt in range(n):
        try:
            html = fetch_game_html(game_id)
            break
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else 0
            if code in (429, 503) and attempt < n - 1:
                wait = backoff_base_s * (2**attempt)
                time.sleep(wait)
                continue
            ms = int((time.perf_counter() - t0) * 1000)
            return "failed", f"HTTP {code}", 0, 0, ms
        except requests.RequestException as e:
            ms = int((time.perf_counter() - t0) * 1000)
            return "failed", str(e)[:500], 0, 0, ms

    if html is None:
        ms = int((time.perf_counter() - t0) * 1000)
        return "failed", "HTTP fetch exhausted retries", 0, 0, ms

    try:
        rows = parse_game_html(html, game_id)
    except Exception as e:
        ms = int((time.perf_counter() - t0) * 1000)
        return "failed", f"parse: {e}"[:500], 0, 0, ms

    if not rows:
        ms = int((time.perf_counter() - t0) * 1000)
        return "failed", "parse: 0 clues", 0, 0, ms

    ins, sk = insert_clues(conn, rows)
    ms = int((time.perf_counter() - t0) * 1000)
    return "complete", None, ins, sk, ms


def run(
    conn: sqlite3.Connection,
    delay_s: float,
    *,
    max_games: int = 0,
    http_retries: int = 4,
    backoff_base_s: float = 30.0,
    log: Callable[[str], None] = print,
) -> dict[str, int]:
    """
    Process crawl_games with status pending or failed; commit after each game.
    ETA uses average seconds per game in this run × games left in this run.
    """
    remaining0 = conn.execute(
        """
        SELECT COUNT(*) FROM crawl_games
        WHERE status IN ('pending', 'failed')
        """
    ).fetchone()[0]
    if remaining0 == 0:
        log("crawl run: nothing to do (no pending or failed games). Run `discover` first.")
        return {"processed": 0, "ok": 0, "failed": 0}

    cap = remaining0 if max_games <= 0 else min(max_games, remaining0)
    log(
        f"crawl run: {remaining0} in queue"
        + (f" (capped to {cap} this run)" if cap < remaining0 else "")
    )

    t_run = time.perf_counter()
    processed = ok = failed = 0
    base_sql = """
        SELECT game_id, season_key, attempts FROM crawl_games
        WHERE status IN ('pending', 'failed')
        ORDER BY game_id
    """
    if max_games > 0:
        rows = conn.execute(base_sql + " LIMIT ?", (int(max_games),)).fetchall()
    else:
        rows = conn.execute(base_sql).fetchall()

    for row in rows:
        gid = int(row["game_id"])
        season_key = row["season_key"]
        attempts = int(row["attempts"])

        if processed > 0:
            polite_delay(delay_s)

        status, err, ins, sk, ms = _scrape_one(
            conn,
            gid,
            http_retries=http_retries,
            backoff_base_s=backoff_base_s,
        )

        now = _utc_now()
        attempts += 1
        if status == "complete":
            conn.execute(
                """
                UPDATE crawl_games SET
                  status = 'complete',
                  error = NULL,
                  attempts = ?,
                  clues_inserted = ?,
                  clues_skipped = ?,
                  last_duration_ms = ?,
                  updated_at = ?
                WHERE game_id = ?
                """,
                (attempts, ins, sk, ms, now, gid),
            )
            ok += 1
        else:
            conn.execute(
                """
                UPDATE crawl_games SET
                  status = 'failed',
                  error = ?,
                  attempts = ?,
                  clues_inserted = NULL,
                  clues_skipped = NULL,
                  last_duration_ms = ?,
                  updated_at = ?
                WHERE game_id = ?
                """,
                (err, attempts, ms, now, gid),
            )
            failed += 1

        conn.commit()
        processed += 1

        elapsed = time.perf_counter() - t_run
        left_in_run = cap - processed
        avg = elapsed / processed if processed else 0.0
        eta_s = avg * left_in_run if processed and left_in_run > 0 else 0.0

        left_all = conn.execute(
            """
            SELECT COUNT(*) FROM crawl_games
            WHERE status IN ('pending', 'failed')
            """
        ).fetchone()[0]
        eta_all_s = avg * left_all if processed and left_all > 0 else 0.0

        eta_note = ""
        if processed >= 1 and left_in_run > 0:
            eta_note = f" ETA_run ~{_fmt_duration(eta_s)} ({left_in_run} left this run)"
        if processed >= 1 and left_all > 0:
            eta_note += f" | ETA_queue ~{_fmt_duration(eta_all_s)} ({left_all} pending+failed)"

        log(
            f"[{processed}/{cap}] game_id={gid} season={season_key!r} "
            f"{status} ins={ins} skip={sk} {ms}ms{eta_note}"
        )

    log(
        f"crawl run done: processed={processed} complete={ok} failed={failed} "
        f"elapsed={_fmt_duration(time.perf_counter() - t_run)}"
    )
    return {"processed": processed, "ok": ok, "failed": failed}


def print_status(conn: sqlite3.Connection, log: Callable[[str], None] = print) -> None:
    c = crawl_counts(conn)
    disc = conn.execute(
        "SELECT value FROM meta WHERE key = 'crawl_discovery_at'"
    ).fetchone()
    when = disc[0] if disc else "never"
    log(
        f"crawl queue: total={c['total']} complete={c['complete']} "
        f"pending={c['pending']} failed={c['failed']} remaining={c['remaining']}"
    )
    log(f"last discover: {when}")
