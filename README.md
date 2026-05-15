# j-answer

“Don’t put your future in Jeopardy!”

Scrape [J-Archive](https://j-archive.com) into SQLite, with an optional web UI for random flashcards and multi-tag full-text search. To run the API and UI on **AWS** (single small EC2, nginx, Let’s Encrypt, DNS e.g. **GoDaddy** for `j-answer.kylemeister.dev`), see **`docs/aws.md`**.

## Requirements

| Component | Requirement |
| --------- | ----------- |
| Scraper / CLI | Python 3.10+ |
| API (e.g. stock Amazon Linux 2023 on EC2) | Python 3.9+ (`python3`); use 3.10+ locally if you prefer |
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
- **Semantic embeddings (optional):** `clue_embeddings` — one vector per clue for vibe / topic search (see below). Format contract lives in `src/embeddings.py`.

---

## Vector embeddings (optional, local)

For semantic “vibe” search (e.g. “US presidents”, “18th century European artists”), you can precompute OpenAI embeddings into the same SQLite file. This is a **one-time local job**; stop the API first if it is using the DB.

### Setup

```bash
pip install -r embed/requirements-embed.txt
```

Set your API key (never commit it):

```powershell
$env:OPENAI_API_KEY = "sk-..."
```

### Run

From the repository root (or `embed/`; default DB is `j-answer.db` at the repo root):

```bash
python embed/vector-embed.py
```

Smoke-test on a small batch before a full run:

```bash
python embed/vector-embed.py --limit 100
```

| Flag | Default | Description |
| ---- | ------- | ----------- |
| `--db` | `j-answer.db` (repo root) | SQLite path |
| `--batch-size` | `1000` | Clues per OpenAI request |
| `--limit` | `0` (no cap) | Max clues to embed this run |
| `--retries` | `5` | Attempts per batch on 429 / 5xx / connection errors |
| `--backoff-base` | `2.0` | Seconds before first retry (doubles each attempt) |
| `-v` | off | Debug logging |

The script is **resumable**: only clues missing from `clue_embeddings` are processed. Progress logs use counts from the start of the run plus rows embedded this session (no full-table recount after every batch).

### Outcome

- **Table:** `clue_embeddings` (`clue_id` → `clues.id`, `ON DELETE CASCADE`, `embedding` BLOB).
- **Does not modify** the `clues` table.
- **Per-clue text embedded:** `game_category | clue_text | answer_text` (same string shape at query time for search).
- **Storage:** `text-embedding-3-small`, **512** dimensions, raw **float32** little-endian bytes (~2 KB per clue). Details: `src/embeddings.py`.
- **Scale:** a full archive is on the order of **~1 GB** of vectors plus OpenAI token charges; use `--limit` to validate first.

Semantic search in the API/UI is not wired up yet; embeddings are the persistence layer for a future phase (cosine similarity, score thresholds, FTS hybrid).

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

Endpoints used by the UI:

| Method | Path | Purpose |
| ------ | ---- | ------- |
| `GET` | `/api/health` | Liveness check |
| `GET` | `/api/random-clue` | One random clue |
| `GET` | `/api/search?tag=a&tag=b` | Full-text search; repeat `tag` for each term (AND across clue text, answer, and in-game category). Optional `limit` (1–500, default 100). |

**CORS:** Local Vite origins are always allowed. For production hosting behind another hostname or split origins, set **`CORS_ORIGINS`** (comma-separated) in the API environment; same-origin nginx + `/api` proxy usually does not require changes. See **`docs/aws.md`** if you deploy to AWS.

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

Static output is in `web/dist/`. For production, prefer **same-origin** nginx: serve `web/dist` and **reverse-proxy** `/api` to Uvicorn (`deploy/nginx-janswer.conf.example`). AWS EC2 steps are in **`docs/aws.md`**.

### Using the UI

- **Search** — type a word or phrase, press Enter or **Add tag**; each tag appears as a removable badge. All tags apply together (**AND**) via SQLite FTS5 over clue text, correct response, and category. Tap a result row to open that clue on the card.
- **I’m feeling lucky** — loads a random clue from the database.
- **Card** — click or tap to flip between clue and answer (keyboard: Enter / Space when focused).

---

## Roadmap

End goal: a web-based, Quizlet-style experience over the full J-Archive-derived corpus, with strong search, AI-assisted taxonomy, and analytics.

| Phase | Scope | Direction |
| ----- | ----- | --------- |
| **1** | Data acquisition & schema | SQLite store, scraper, FTS5 on clue / answer / category — **done** |
| **2** | Core flashcard UI | Random clue, flip animation, Jeopardy-style presentation — **done** |
| **3** | Search & filtering | Multi-tag AND search, result list → card; FTS-backed — **done** for MVP (pagination, highlights, round/date filters later) |
| **4** | AI categorization | Batch LLM pass to fill `ai_category` / `ai_subcategory` from a controlled taxonomy; storage and re-runs |
| **5** | Smarter search | Semantic / embedding-backed search (vectors in `clue_embeddings`; batch job in `embed/`) — **storage in progress**; API search TBD |
| **6** | Statistics & taxonomy UI | Hierarchy by AI categories, counts per node, “study this bucket” random sessions |

Principles: iterate in phases; keep scraping and persistence separate from the UI; call out J-Archive rate limits and HTML quirks early; keep interactions snappy and mobile-friendly.

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
  search.py         # FTS5 multi-tag search helpers
  embeddings.py     # clue_embeddings BLOB format + schema helpers
embed/
  vector-embed.py   # local batch embed job (OpenAI)
  requirements-embed.txt
web/                # Vite + React flashcard SPA
deploy/             # nginx + systemd examples for production
docs/               # AWS deployment (EC2, CloudFormation) — see docs/aws.md
infra/              # CloudFormation template (EC2 + EIP + optional Route 53)
.github/workflows/ # CI deploy (EC2 over SSH)
requirements.txt
```

`python -m janswer` loads `janswer/__main__.py`, which calls into `src/__main__.py` so the working directory stays the repo root and imports remain stable.
