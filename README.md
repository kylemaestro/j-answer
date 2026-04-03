# j-answer

“Don’t put your future in Jeopardy!”

Scrape [J-Archive](https://j-archive.com) into SQLite, with an optional web UI for random flashcards.

## Requirements

| Component | Requirement |
| --------- | ----------- |
| Scraper / API | Python 3.10+ |
| Scraping | Network access to `j-archive.com` |
| Web UI | Node.js 20+ and npm (or any toolchain that can run Vite 6) |

## Install

From the repository root (contains `requirements.txt`, `janswer/`, and `src/`):

```bash
pip install -r requirements.txt
```

## Command-line interface

Run the CLI as a module so imports resolve:

```bash
python -m janswer [global options] <command> ...
```

Global options must appear **before** the subcommand (e.g. before `season`, `crawl`, …).

### Global options

| Option | Default | Used by | Description |
| ------ | ------- | ------- | ----------- |
| `--db` | `j-answer.db` | All | Path to the SQLite database file. |
| `--delay` | `1.5` | `season`, `crawl discover`, `crawl run` | Seconds to wait between HTTP requests in loops (polite pacing toward the site). **Ignored** by `game` and `crawl status` (single request or DB-only). |

---

### `game` — scrape one episode

Fetches a single game page by J-Archive `game_id` and inserts clues. Duplicates are skipped (`jarchive_clue_id` is unique).

**Default (minimal):**

```bash
python -m janswer game 1
```

**Optional:** `--db`, `--delay` (accepted globally; `--delay` has no effect for this command).

---

### `season` — scrape every game linked from a season page

Season numbers match J-Archive (e.g. [Season 1](https://j-archive.com/showseason.php?season=1)).

**Default (minimal):**

```bash
python -m janswer season 1
```

**Optional:**

| Flag | Default | Description |
| ---- | ------- | ----------- |
| `--limit N` | `0` (no cap) | Process at most `N` games from that season’s listing. |
| `--db` | `j-answer.db` | Database path. |
| `--delay` | `1.5` | Pause between consecutive game requests within the season. |

---

### `crawl` — resumable full-archive queue

Three subcommands. Global `--db` / `--delay` apply where noted.

#### `crawl discover`

Walks `listseasons.php` and each season listing; registers `game_id`s into `crawl_games` (`INSERT OR IGNORE`). Safe to re-run; does not wipe completed rows.

**Default (minimal):**

```bash
python -m janswer crawl discover
```

**Optional:**

| Flag | Default | Description |
| ---- | ------- | ----------- |
| `--max-seasons N` | `0` | Only process the first `N` seasons from the index. `0` means all seasons (useful for dry runs). |
| `--db` | `j-answer.db` | Database path. |
| `--delay` | `1.5` | Pause between season-page requests during discovery. |

#### `crawl run`

Processes rows in `crawl_games` with status `pending` or `failed`, one game per transaction (resumable). If the queue is empty, run `crawl discover` first.

**Default (minimal):**

```bash
python -m janswer crawl run
```

**Optional:**

| Flag | Default | Description |
| ---- | ------- | ----------- |
| `--max-games N` | `0` | Cap this run at `N` games. `0` means all currently `pending` + `failed`. |
| `--http-retries` | `4` | **How many HTTP attempts** to fetch each game’s HTML. Non-retryable failures stop immediately; **429** (rate limit) and **503** (service unavailable) trigger waits between attempts (see `--backoff-base`). |
| `--backoff-base` | `30.0` | **Seconds to wait before the first retry** after a 429 or 503. Each subsequent retry **doubles** the wait: \(30, 60, 120, …\) seconds (capped by how many retries remain). |
| `--db` | `j-answer.db` | Database path. |
| `--delay` | `1.5` | Pause between games while draining the queue. |

#### `crawl status`

Prints queue counts (`pending`, `failed`, `complete`, …) and last discover timestamp. No HTTP scraping.

**Default (minimal):**

```bash
python -m janswer crawl status
```

**Optional:** `--db` only (`--delay` has no effect).

---

## Example: full Season 1

Imports every game listed on the Season 1 page into the default database using default pacing:

```bash
cd /path/to/j-answer
pip install -r requirements.txt
python -m janswer season 1
```

To scrape more gently (longer delay) or only a sample:

```bash
python -m janswer --delay 2.5 season 1
python -m janswer season 1 --limit 5
```

Re-running the same season is safe: existing clues are skipped.

**Windows (PowerShell):** same commands if `python` is on your `PATH`:

```powershell
cd C:\path\to\j-answer
pip install -r requirements.txt
python -m janswer season 1
```

---

## Database

- **Schema** is created on first run (`init_schema` in `src/db.py`).
- **Full-text search:** `clues_fts` (FTS5), kept in sync with `clues` via triggers.
- **Crawl queue:** `crawl_games` (schema version 2).

---

## Web application (flashcards)

The `web/` app is a Vite + React + Tailwind SPA. It loads random clues from SQLite via **`src/api_app.py`** (FastAPI).

### Prerequisites

- Dependencies from `pip install -r requirements.txt` (includes FastAPI and Uvicorn).
- A populated SQLite file (default path below) or `JANSWER_DB` pointing at one.
- Node.js and npm for the frontend.

### Environment

| Variable | Purpose |
| -------- | ------- |
| `JANSWER_DB` | Optional. Absolute or relative path to the SQLite file. If unset, the API uses `j-answer.db` in the **repository root** (same default as the CLI). |

### API server

From the repository root:

**Development** (auto-reload on code changes):

```bash
python -m uvicorn src.api_app:app --reload --host 127.0.0.1 --port 8000
```

**Production-style** (no reload, multiple workers optional):

```bash
python -m uvicorn src.api_app:app --host 0.0.0.0 --port 8000
```

Endpoints used by the UI: `GET /api/random-clue`, `GET /api/health`. CORS is restricted to local dev origins; for a public deployment, tighten or extend CORS in `src/api_app.py` and serve the SPA and API under a reverse proxy (same origin or explicit proxy rules).

### Frontend

**Development** — proxies `/api` to `http://127.0.0.1:8000` (see `web/vite.config.ts`):

```bash
cd web
npm install
npm run dev
```

Open the URL Vite prints (typically `http://127.0.0.1:5173`).

**Production build:**

```bash
cd web
npm install
npm run build
```

Static output is in `web/dist/`. Serve it with any static file host and **reverse-proxy** `/api` to the Uvicorn process so the browser can call `/api/random-clue` on the same origin as the HTML (or reconfigure the client base URL in a future change).

### Using the UI

- **I’m feeling lucky** — fetches a random clue from the database.
- **Click or tap the card** — flips between clue and answer (keyboard: Enter / Space when focused).

---

## Project layout

```
janswer/
  __init__.py
  __main__.py       # `python -m janswer` entry (delegates to src)
src/
  __init__.py
  __main__.py       # CLI implementation
  api_app.py        # FastAPI backend for the web UI
  crawl.py
  db.py
  parser.py
  scraper.py
web/                # Vite + React flashcard SPA
requirements.txt
```

`python -m janswer` loads `janswer/__main__.py`, which calls into `src/__main__.py` so the working directory stays the repo root and imports remain stable.
