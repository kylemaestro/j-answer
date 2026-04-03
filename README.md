# j-answer

"Don't put your future in Jeopardy!"

## Requirements

- Python 3.10+
- Network access to `j-archive.com` (for scraping only)
- **Phase 2 (flashcard UI):** Node.js 20+ and npm (or another package manager compatible with Vite)

## Install

From the repository root (the directory that contains `requirements.txt` and the `janswer` package folder):

```bash
pip install -r requirements.txt
```

## Run from the terminal

All commands use the module form so imports resolve correctly:

```bash
python -m janswer [global options] <command> ...
```

### Global options


| Option    | Default       | Description                                                                     |
| --------- | ------------- | ------------------------------------------------------------------------------- |
| `--db`    | `j-answer.db` | Path to the SQLite database file                                                |
| `--delay` | `1.5`         | Seconds to wait between HTTP requests (where the command loops over many pages) |


Place `--db` and `--delay` **before** the subcommand name.

### Commands

#### `game` — one game by `game_id`

```bash
python -m janswer game 1
```

#### `season` — every game linked from a season page

Season numbers match J-Archive (e.g. [Season 1](https://j-archive.com/showseason.php?season=1)). Use `--limit` only if you want a subset; `0` or omitting it means **all** games on that season’s page.

```bash
python -m janswer --delay 2 season 1
python -m janswer --delay 2 season 1 --limit 5
```

#### `crawl` — resumable full-archive queue

Global options must come **before** `crawl`:

```bash
python -m janswer --delay 2 crawl discover
python -m janswer --delay 2 crawl run
python -m janswer crawl status
```

- `**crawl discover**` — reads `listseasons.php` and each season listing; registers `game_id`s in the DB (safe to re-run; does not wipe completed rows).
- `**crawl run**` — scrapes `pending` and `failed` games, one commit per game (resumable after interrupt or errors). Optional: `--max-games`, `--http-retries`, `--backoff-base`.
- `**crawl status**` — queue counts and last discover time.

---

## Example: entire first season (Season 1)

This pulls **all** games listed on the Season 1 archive page into `j-answer.db`, with a 2-second pause between game requests to reduce load on the server.

```bash
cd /path/to/janswer
pip install -r requirements.txt
python -m janswer --delay 2 season 1
```

You will see one line per game (`game_id`, clues inserted/skipped) and a final summary. Re-running the same command is safe: duplicate clues are skipped via unique `jarchive_clue_id` keys.

On Windows (PowerShell), the same invocation works if `python` is on your `PATH`:

```powershell
cd C:\path\to\janswer
pip install -r requirements.txt
python -m janswer --delay 2 season 1
```

## Database

- Schema is created automatically on first run (`init_schema`).
- Full-text search lives in the `clues_fts` FTS5 table (kept in sync with `clues` via triggers).
- The crawl queue uses the `crawl_games` table (after schema v2).

## Phase 2: Flashcard web UI

The UI lives in `web/` (Vite + React + Tailwind). It reads clues from SQLite through a small FastAPI app in `src/api_app.py`.

1. **Populate the database** (see the scraper commands above) so `j-answer.db` exists at the repo root, or set `**JANSWER_DB`** to the full path of your SQLite file.
2. **Start the API** (from the repository root):
  ```bash
   python -m uvicorn src.api_app:app --reload --host 127.0.0.1 --port 8000
  ```
3. **Start the frontend** (separate terminal):
  ```bash
   cd web
   npm install
   npm run dev
  ```
   Open the URL Vite prints (usually `http://127.0.0.1:5173`). The dev server proxies `/api/*` to the API on port 8000.
4. Use **I’m feeling lucky** to load a random clue, **tap or click the card** to flip (answer on the back). On desktop, the card tilts slightly with the pointer (Balatro-style parallax).

**Production build:** `cd web && npm run build` then serve `web/dist/` with any static host. You still need the API reachable at the same origin or configure the static host to proxy `/api` to Uvicorn.

## Project layout

```
janswer/
  __main__.py    # Launcher so `python -m janswer` works
src/
  __init__.py
  __main__.py    # CLI entry
  api_app.py     # FastAPI: random clue for the web UI
  crawl.py       # Resumable crawl + ETA
  db.py          # SQLite + FTS
  parser.py      # HTML → clue rows
  scraper.py     # HTTP helpers
web/             # React flashcard app (Phase 2)
requirements.txt
```

## Notes

- `python -m janswer` runs the `janswer` module launcher, which delegates to the CLI implementation in `src/__main__.py`.
This keeps imports stable and avoids path issues from running a file directly.

