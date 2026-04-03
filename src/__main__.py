"""CLI: scrape J-Archive into SQLite."""

from __future__ import annotations

import argparse
import sys

from src.crawl import discover, print_status, run as crawl_run
from src.db import connect, init_schema, insert_clues
from src.parser import parse_game_html
from src.scraper import (
    DEFAULT_DELAY_S,
    fetch_game_html,
    fetch_season_html,
    parse_game_ids_from_season_page,
    polite_delay,
)

CRAWL_BANNER = """       ░▒▓█▓▒░▒▓████████▓▒░▒▓██████▓▒░░▒▓███████▓▒░ ░▒▓██████▓▒░░▒▓███████▓▒░░▒▓███████▓▒░░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░
       ░▒▓█▓▒░▒▓█▓▒░     ░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░
       ░▒▓█▓▒░▒▓█▓▒░     ░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░
       ░▒▓█▓▒░▒▓██████▓▒░░▒▓█▓▒░░▒▓█▓▒░▒▓███████▓▒░░▒▓████████▓▒░▒▓███████▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓██████▓▒░░▒▓█▓▒░
░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░     ░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░  ░▒▓█▓▒░   ░▒▓█▓▒░
░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░     ░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░  ░▒▓█▓▒░
 ░▒▓██████▓▒░░▒▓████████▓▒░▒▓██████▓▒░░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░▒▓███████▓▒░   ░▒▓█▓▒░   ░▒▓█▓▒░"""
ANSI_HEX_2596BE = "\033[38;2;37;150;190m"
ANSI_RESET = "\033[0m"


def print_crawl_banner() -> None:
    colored_banner = f"{ANSI_HEX_2596BE}{CRAWL_BANNER}{ANSI_RESET}\n"
    try:
        print(colored_banner, end="")
    except UnicodeEncodeError:
        # Windows terminals may default to cp1252; write UTF-8 bytes directly.
        sys.stdout.buffer.write(colored_banner.encode("utf-8"))
        sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Scrape j-archive.com into SQLite.")
    p.add_argument(
        "--db",
        default="j-answer.db",
        help="SQLite database path (default: j-answer.db)",
    )
    p.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY_S,
        help=f"Seconds between HTTP requests (default {DEFAULT_DELAY_S})",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    one = sub.add_parser("game", help="Scrape a single game by game_id")
    one.add_argument("game_id", type=int)

    season = sub.add_parser("season", help="Scrape games listed on a season page")
    season.add_argument("season", type=int, help="Season number (see listseasons.php)")
    season.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max games (0 = all listed on the season page)",
    )

    crawl_p = sub.add_parser(
        "crawl",
        help="Resumable full-archive crawl (discover seasons, then run queue)",
        epilog=(
            "Global options like --db and --delay go before `crawl`, e.g. "
            "`python -m janswer crawl run` "
            "or `python -m janswer --db custom.db crawl status`."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    crawl_sub = crawl_p.add_subparsers(dest="crawl_cmd", required=True)

    disc = crawl_sub.add_parser(
        "discover",
        help="Fetch listseasons + each season page; register game_ids (safe to re-run)",
    )
    disc.add_argument(
        "--max-seasons",
        type=int,
        default=0,
        help="Only first N seasons (0 = all); useful for testing",
    )

    crun = crawl_sub.add_parser(
        "run",
        help="Scrape pending/failed games with per-game checkpoint + ETA",
    )
    crun.add_argument(
        "--max-games",
        type=int,
        default=0,
        help="Max games this run (0 = all pending+failed)",
    )
    crun.add_argument(
        "--http-retries",
        type=int,
        default=4,
        help="HTTP attempts per game (429/503 use exponential backoff)",
    )
    crun.add_argument(
        "--backoff-base",
        type=float,
        default=30.0,
        help="Base seconds before first retry on 429/503 (doubles each retry)",
    )

    crawl_sub.add_parser("status", help="Show crawl queue counts")

    args = p.parse_args(argv)
    conn = connect(args.db)
    init_schema(conn)

    if args.cmd == "game":
        html = fetch_game_html(args.game_id)
        rows = parse_game_html(html, args.game_id)
        ins, sk = insert_clues(conn, rows)
        print(f"game_id={args.game_id}: parsed {len(rows)} clues, inserted {ins}, skipped {sk}")
        return 0

    if args.cmd == "season":
        html = fetch_season_html(args.season)
        gids = parse_game_ids_from_season_page(html)
        if args.limit > 0:
            gids = gids[: args.limit]
        total_ins = total_sk = 0
        for i, gid in enumerate(gids):
            if i > 0:
                polite_delay(args.delay)
            gh = fetch_game_html(gid)
            rows = parse_game_html(gh, gid)
            ins, sk = insert_clues(conn, rows)
            total_ins += ins
            total_sk += sk
            print(f"game_id={gid}: parsed {len(rows)} clues, inserted {ins}, skipped {sk}")
        print(f"Season {args.season}: total inserted {total_ins}, skipped {total_sk}")
        return 0

    if args.cmd == "crawl":
        if args.crawl_cmd == "discover":
            print_crawl_banner()
            n_seasons, added = discover(
                conn,
                args.delay,
                max_seasons=getattr(args, "max_seasons", 0) or 0,
            )
            print(f"discover: season_pages={n_seasons} new_rows_added={added}")
            return 0
        if args.crawl_cmd == "run":
            print_crawl_banner()
            crawl_run(
                conn,
                args.delay,
                max_games=getattr(args, "max_games", 0) or 0,
                http_retries=getattr(args, "http_retries", 4),
                backoff_base_s=getattr(args, "backoff_base", 30.0),
            )
            return 0
        if args.crawl_cmd == "status":
            print_status(conn)
            return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
