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
- **Semantic embeddings (optional):** `clue_embeddings` (source BLOBs) plus `clue_vec_index` ([sqlite-vec](https://github.com/asg017/sqlite-vec) `vec0` KNN table) for Magic search. Format contract lives in `src/embeddings.py`.

---

## Vector embeddings (optional, local)

For semantic “vibe” search (e.g. “US presidents”, “18th century European artists”), you can precompute OpenAI embeddings into the same SQLite file. This is a **one-time local job**; stop the API first if it is using the DB.

### Setup

```bash
pip install -r requirements.txt
pip install -r embed/requirements-embed.txt
```

(`sqlite-vec` is included in both; the API needs it for Magic search at runtime.)

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

### Build the vec index (required for Magic search)

After embeddings exist (or when upgrading an older DB), build the sqlite-vec index **once per database** (stop the API first if it holds the file open):

```bash
python scripts/migrate_vec_index.py --db j-answer.db
```

| Flag | Default | Description |
| ---- | ------- | ----------- |
| `--db` | `j-answer.db` (repo root) | SQLite path |
| `--batch-size` | `5000` | Rows per upsert batch |
| `-v` | off | Debug logging |

`vector-embed.py` updates `clue_vec_index` incrementally when sqlite-vec is installed. Re-run the migration after copying a DB that only has `clue_embeddings`, or to rebuild from scratch.

### Outcome

- **Tables:** `clue_embeddings` (`clue_id` → `clues.id`, `ON DELETE CASCADE`, `embedding` BLOB) and `clue_vec_index` (`vec0`, cosine KNN).
- **Does not modify** the `clues` table.
- **Per-clue text embedded:** `game_category | clue_text | answer_text` (same string shape at query time for search).
- **Storage:** `text-embedding-3-small`, **512** dimensions, raw **float32** little-endian bytes (~2 KB per clue). Details: `src/embeddings.py`.
- **Scale:** a full archive is on the order of **~1 GB** of vectors plus OpenAI token charges; use `--limit` to validate first.

The web UI **Magic** mode calls `/api/search/magic` (cosine similarity + score threshold). **Exact** mode keeps FTS tag search unchanged.

**Search-time API usage:** Clue vectors are stored in `clue_embeddings` and indexed in `clue_vec_index`. Each Magic search calls OpenAI **once** to embed the query, then runs a **sqlite-vec KNN** query (`MATCH` + `k`) — fast on small instances (e.g. `t4g.micro`) compared to scanning the full corpus in Python.

**Future enhancement:** **local query embedding** so Magic search needs no OpenAI call after the batch job (re-embed clues with the same local model so spaces match).

### Production (EC2 via Session Manager)

On **Amazon Linux 2023** (e.g. `t4g.micro` from `docs/aws.md`), use **AWS Systems Manager Session Manager** instead of SSH if you prefer. From your laptop (AWS CLI v2, same region as the instance):

1. **Find the instance ID** (stack output or console):

   ```bash
   aws ec2 describe-instances --region us-east-1 \
     --filters "Name=tag:aws:cloudformation:stack-name,Values=j-answer-app" \
     --query "Reservations[].Instances[].InstanceId" --output text
   ```

2. **Start an interactive shell** on the box:

   ```bash
   aws ssm start-session --target i-0123456789abcdef0 --region us-east-1
   ```

   You land as `ssm-user` or similar; switch to the app user and directory used in deploy:

   ```bash
   sudo -iu ec2-user
   cd /opt/j-answer/app
   source venv/bin/activate
   ```

3. **Install / upgrade dependencies** (includes `sqlite-vec`):

   ```bash
   git pull
   pip install -r requirements.txt
   ```

4. **Stop the API** while the DB is migrated (avoids SQLite lock errors):

   ```bash
   sudo systemctl stop janswer-api
   ```

5. **Build or rebuild the vec index** on the production DB:

   ```bash
   python scripts/migrate_vec_index.py --db /opt/j-answer/data/j-answer.db -v
   ```

   Expect a few minutes for a full archive (~500k vectors). Check counts:

   ```bash
   sqlite3 /opt/j-answer/data/j-answer.db \
     "SELECT (SELECT COUNT(*) FROM clue_embeddings) AS blobs,
             (SELECT COUNT(*) FROM clue_vec_index) AS indexed;"
   ```

6. **Start the API** and smoke-test Magic search:

   ```bash
   sudo systemctl start janswer-api
   curl -sS "http://127.0.0.1:8000/api/embeddings/status"
   curl -sS "http://127.0.0.1:8000/api/search/magic?q=US%20presidents&limit=3"
   ```

7. **Exit** the session: `exit` twice (ec2-user shell, then SSM).

**Uploading a new database from your laptop:** stop the API on EC2, copy `j-answer.db` to `/opt/j-answer/data/` (e.g. `scp` or CI rsync from `docs/aws.md`), run `migrate_vec_index.py` on the instance, then start the API.

**One-liner remote migrate** (no interactive shell; replace instance ID):

```bash
aws ssm send-command --region us-east-1 \
  --instance-ids i-0123456789abcdef0 \
  --document-name AWS-RunShellScript \
  --parameters commands='[
    "sudo systemctl stop janswer-api",
    "sudo -u ec2-user bash -lc \"cd /opt/j-answer/app && source venv/bin/activate && pip install -r requirements.txt && python scripts/migrate_vec_index.py --db /opt/j-answer/data/j-answer.db\"",
    "sudo systemctl start janswer-api"
  ]'
```

Poll status with `aws ssm list-command-invocations` / `get-command-invocation`.

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
| `OPENAI_API_KEY` | Required for **Magic** search on the **API process** (see below). Not read by the browser or Vite—only by Uvicorn/`src/semantic_search.py`. |
| `JANSWER_MAGIC_MIN_SCORE` | Optional. Minimum cosine similarity for Magic results (default `0.45`; UI slider overrides per request). |

Setting the key for the embed script (`vector-embed.py`) does **not** automatically apply to the API. Use the **same terminal session** (or set the variable in your IDE run configuration) **before** starting Uvicorn, then restart the API if it was already running.

**Windows (PowerShell):**

```powershell
$env:OPENAI_API_KEY = "sk-..."   # your key
python -m uvicorn src.api_app:app --reload --host 127.0.0.1 --port 8000
```

**macOS / Linux:**

```bash
export OPENAI_API_KEY="sk-..."
python -m uvicorn src.api_app:app --reload --host 127.0.0.1 --port 8000
```

Quick check (from another terminal, API must be up):

```bash
curl "http://127.0.0.1:8000/api/search/magic?q=test"
```

If the key is missing you get `503` with `OPENAI_API_KEY is not set on the API server`. The app does not load a `.env` file automatically—use your shell or IDE env settings (or add dotenv later if you want).

### API server

From the repository root (with `OPENAI_API_KEY` set in that environment if you use Magic search):

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
| `GET` | `/api/search?tag=a&tag=b` | **Exact** full-text search; repeat `tag` for each term (AND). Optional `limit` (1–500, default 100). |
| `GET` | `/api/search/magic?q=...` | **Magic** semantic search over clues in `clue_embeddings` only. Requires `OPENAI_API_KEY` on the API host. Optional `limit`, `min_score` (default `0.45` or `JANSWER_MAGIC_MIN_SCORE`). |
| `GET` | `/api/embeddings/status` | `embedded`, `vec_indexed`, and `magic_available` (both BLOBs and vec index must be present). |

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

- **Search** — toggle **Exact** (tags + FTS5, same as before) or **Magic** (natural-language vibe search over embedded clues only, with a **confidence** slider for minimum similarity, default 0.45). Magic requires embeddings in the DB and `OPENAI_API_KEY` when running the API. Tap a result row to open that clue on the card.
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
| **5** | Smarter search | Magic vibe search via sqlite-vec KNN; local query embedding + hybrid with FTS later |
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
  semantic_search.py # Magic search (sqlite-vec vec0 KNN)
  vec_index.py      # clue_vec_index build/sync helpers
  embeddings.py     # clue_embeddings BLOB format + schema helpers
scripts/
  migrate_vec_index.py  # one-shot rebuild of clue_vec_index from BLOBs
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
