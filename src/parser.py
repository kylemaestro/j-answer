"""Parse J-Archive showgame.php HTML into clue records."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from bs4 import BeautifulSoup

_TITLE_DATE = re.compile(
    r"Show #(?P<show>\d+)\s*-\s*(?P<weekday>\w+),\s*(?P<rest>.+)$",
    re.IGNORECASE,
)
_CLUE_ID = re.compile(r"clue_id=(\d+)")
_DOLLARS = re.compile(r"\$?\s*([\d,]+)")


def _parse_air_date(soup: BeautifulSoup) -> tuple[date | None, int | None]:
    h1 = soup.select_one("#game_title h1")
    if not h1:
        return None, None
    text = h1.get_text(strip=True)
    m = _TITLE_DATE.match(text)
    if not m:
        return None, None
    show_num = int(m.group("show"))
    rest = m.group("rest").strip()
    for fmt in ("%B %d, %Y", "%B %d, %y"):
        try:
            return datetime.strptime(rest, fmt).date(), show_num
        except ValueError:
            continue
    return None, show_num


def _extract_clue_id(clue_td) -> int | None:
    link = clue_td.find("a", href=_CLUE_ID)
    if not link or not link.get("href"):
        return None
    m = _CLUE_ID.search(link["href"])
    return int(m.group(1)) if m else None


def _parse_value_cells(clue_td) -> tuple[str | None, int | None, bool]:
    dd = clue_td.find("td", class_="clue_value_daily_double")
    norm = clue_td.find("td", class_="clue_value")
    if dd:
        raw = dd.get_text(strip=True)
        m = _DOLLARS.search(raw)
        amt = int(m.group(1).replace(",", "")) if m else None
        return raw, amt, True
    if norm:
        raw = norm.get_text(strip=True)
        m = _DOLLARS.search(raw)
        amt = int(m.group(1).replace(",", "")) if m else None
        return raw, amt, False
    return None, None, False


def _clue_and_answer(clue_td) -> tuple[str, str] | None:
    front = None
    back = None
    for cell in clue_td.find_all("td", class_="clue_text"):
        cid = cell.get("id") or ""
        if cid.endswith("_r"):
            em = cell.find(class_="correct_response")
            if em:
                back = em.get_text(" ", strip=True)
        else:
            front = cell.get_text(" ", strip=True)
    if not front or back is None:
        return None
    return front, back


def _category_names_from_row(first_tr) -> list[str]:
    names: list[str] = []
    for cat_td in first_tr.find_all("td", class_="category", recursive=False):
        el = cat_td.find(class_="category_name")
        if el:
            names.append(el.get_text(strip=True))
    return names


def _parse_board_round(
    round_div,
    round_key: str,
    game_id: int,
    air: date,
    show_number: int | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    table = round_div.find("table", class_="round")
    if not table:
        return out
    rows = table.find_all("tr", recursive=False)
    if not rows:
        return out
    categories = _category_names_from_row(rows[0])
    if not categories:
        return out

    for tr in rows[1:]:
        cells = tr.find_all("td", class_="clue", recursive=False)
        for col_idx, clue_td in enumerate(cells):
            if col_idx >= len(categories):
                break
            cat = categories[col_idx]
            clue_id = _extract_clue_id(clue_td)
            if clue_id is None:
                continue
            pair = _clue_and_answer(clue_td)
            if not pair:
                continue
            clue_text, answer_text = pair
            if not clue_text.strip():
                continue
            value_display, value_amount, is_dd = _parse_value_cells(clue_td)
            out.append(
                {
                    "jarchive_clue_id": clue_id,
                    "jarchive_game_id": game_id,
                    "show_number": show_number,
                    "air_date": air.isoformat(),
                    "round": round_key,
                    "game_category": cat,
                    "value_display": value_display,
                    "value_amount": value_amount,
                    "is_daily_double": 1 if is_dd else 0,
                    "clue_text": clue_text,
                    "answer_text": answer_text,
                    "ai_category": None,
                    "ai_subcategory": None,
                }
            )
    return out


def _parse_final_round(
    round_div,
    game_id: int,
    air: date,
    show_number: int | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    table = round_div.find("table", class_="final_round")
    if not table:
        return out
    cat_el = table.find(class_="category_name")
    category = cat_el.get_text(strip=True) if cat_el else "FINAL JEOPARDY"
    clue_td = table.find("td", class_="clue")
    if not clue_td:
        return out
    pair = _clue_and_answer(clue_td)
    if not pair:
        return out
    clue_text, answer_text = pair
    if not clue_text.strip():
        return out
    synthetic_id = -int(game_id)
    out.append(
        {
            "jarchive_clue_id": synthetic_id,
            "jarchive_game_id": game_id,
            "show_number": show_number,
            "air_date": air.isoformat(),
            "round": "final_jeopardy",
            "game_category": category,
            "value_display": None,
            "value_amount": None,
            "is_daily_double": 0,
            "clue_text": clue_text,
            "answer_text": answer_text,
            "ai_category": None,
            "ai_subcategory": None,
        }
    )
    return out


def parse_game_html(html: str, game_id: int) -> list[dict[str, Any]]:
    """
    Parse a full showgame.php HTML document.

    Returns a list of dicts ready for :meth:`janswer.db.insert_clues`.
    """
    soup = BeautifulSoup(html, "html.parser")
    air, show_number = _parse_air_date(soup)
    if air is None:
        raise ValueError("Could not parse air date from #game_title h1")

    clues: list[dict[str, Any]] = []
    jr = soup.select_one("#jeopardy_round")
    if jr:
        clues.extend(_parse_board_round(jr, "jeopardy", game_id, air, show_number))
    dj = soup.select_one("#double_jeopardy_round")
    if dj:
        clues.extend(_parse_board_round(dj, "double_jeopardy", game_id, air, show_number))
    fj = soup.select_one("#final_jeopardy_round")
    if fj:
        clues.extend(_parse_final_round(fj, game_id, air, show_number))
    return clues
