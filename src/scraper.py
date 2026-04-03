"""HTTP fetch and season listing for J-Archive."""

from __future__ import annotations

import re
import time
from typing import Iterable
from urllib.parse import quote, unquote

import requests
from bs4 import BeautifulSoup

BASE = "https://j-archive.com"
DEFAULT_DELAY_S = 1.5
USER_AGENT = "janswer/0.1 (local; respectful scrape; default delay 1.5s)"

_SESSION: requests.Session | None = None


def _session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
    return _SESSION


def fetch_game_html(game_id: int, timeout: float = 30.0) -> str:
    url = f"{BASE}/showgame.php?game_id={game_id}"
    r = _session().get(url, timeout=timeout)
    r.raise_for_status()
    return r.text


def fetch_listseasons_html(timeout: float = 30.0) -> str:
    url = f"{BASE}/listseasons.php"
    r = _session().get(url, timeout=timeout)
    r.raise_for_status()
    return r.text


def fetch_season_html(season: int | str, timeout: float = 30.0) -> str:
    key = str(season)
    safe = quote(key, safe="")
    url = f"{BASE}/showseason.php?season={safe}"
    r = _session().get(url, timeout=timeout)
    r.raise_for_status()
    return r.text


def parse_season_keys_from_list(html: str) -> list[str]:
    """
    Season keys from listseasons.php (numeric seasons and specials like cwcpi, jm).
    Order preserved; duplicates (e.g. navbar vs table) removed.
    """
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    out: list[str] = []
    for a in soup.select('a[href*="showseason.php?season="]'):
        href = a.get("href") or ""
        m = re.search(r"[?&]season=([^&]+)", href)
        if not m:
            continue
        key = unquote(m.group(1))
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


_GAME_ID_RE = re.compile(r"showgame\.php\?game_id=(\d+)")


def parse_game_ids_from_season_page(html: str) -> list[int]:
    """Extract unique game_id values from a showseason.php page (order preserved)."""
    seen: set[int] = set()
    out: list[int] = []
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select('a[href*="showgame.php?game_id="]'):
        href = a.get("href") or ""
        m = _GAME_ID_RE.search(href)
        if not m:
            continue
        gid = int(m.group(1))
        if gid not in seen:
            seen.add(gid)
            out.append(gid)
    return out


def polite_delay(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def fetch_games(
    game_ids: Iterable[int],
    delay_s: float = DEFAULT_DELAY_S,
) -> Iterable[tuple[int, str]]:
    """Yield (game_id, html) for each id; pauses between requests."""
    ids = list(game_ids)
    for i, gid in enumerate(ids):
        if i > 0:
            polite_delay(delay_s)
        yield gid, fetch_game_html(gid)
