# j-answer

"Don't put your future in Jeopardy!"

j-answer scrapes the [J-Archive](https://j-archive.com) into a single SQLite
database and serves it as a flashcard web app. It offers three ways to find
clues: a random draw, exact full-text tag search, and semantic ("Magic")
search over OpenAI embeddings.

## Components

- **Scraper / CLI** (`janswer`, `src/`) — builds and maintains the SQLite
  corpus from j-archive.com.
- **API** (`src/api_app.py`) — FastAPI backend: random clue, FTS5 tag search,
  semantic search.
- **Web UI** (`web/`) — Vite + React + Tailwind flashcard SPA.
- **Embeddings** (`embed/`, `scripts/`) — optional OpenAI embeddings indexed
  with sqlite-vec (binary-quantized) for Magic search.

## Requirements

| Component | Requirement |
| --------- | ----------- |
| Scraper / CLI | Python 3.10+ |
| API | Python 3.9+ (`python3`) |
| Scraping | Network access to `j-archive.com` |
| Web UI | Node.js 20+ and npm |
| Magic search | An OpenAI API key (`text-embedding-3-small`) |

## Quick start

```bash
# 1. install
pip install -r requirements.txt

# 2. scrape a season into j-answer.db
python -m janswer season 1

# 3. run the API (repo root)
python -m uvicorn src.api_app:app --host 127.0.0.1 --port 8000

# 4. run the web UI (in another shell)
cd web && npm install && npm run dev
```

Open the URL Vite prints (typically `http://127.0.0.1:5173`). Exact search and
"I'm feeling lucky" work with any scraped data; Magic search additionally needs
embeddings (see `docs/search.md`) and `OPENAI_API_KEY` set on the API process.

## Documentation

| Topic | File |
| ----- | ---- |
| Scraping and CLI reference | `docs/scraping.md` |
| Database and schema | `docs/database.md` |
| Search and embeddings (architecture, index builds) | `docs/search.md` |
| Local development (API + web, env vars, endpoints) | `docs/development.md` |
| Production deploy on AWS (EC2, nginx, TLS) | `docs/aws.md` |
| Known bugs | `docs/bugs.md` |

## Project layout

```
janswer/            # `python -m janswer` entry (delegates to src/)
src/
  __main__.py       # CLI implementation
  api_app.py        # FastAPI backend
  crawl.py          # resumable scrape queue
  db.py             # connection + schema
  parser.py         # J-Archive HTML parsing
  scraper.py        # HTTP fetching
  search.py         # FTS5 tag search
  semantic_search.py# Magic search (hamming KNN + float rerank)
  vec_index.py      # clue_vec_index (sqlite-vec) build/sync
  embeddings.py     # clue_embeddings BLOB format + bit packing
scripts/
  migrate_vec_index.py  # rebuild clue_vec_index from embeddings
embed/
  vector-embed.py   # local batch embed job (OpenAI)
web/                # Vite + React flashcard SPA
deploy/             # nginx + systemd examples
docs/               # scraping, database, search, development, aws, bugs
infra/              # CloudFormation template (EC2 + EIP + optional Route 53)
```

## Roadmap

| Phase | Scope | Status |
| ----- | ----- | ------ |
| 1 | Data acquisition and schema (SQLite, scraper, FTS5) | done |
| 2 | Core flashcard UI (random clue, flip, presentation) | done |
| 3 | Search and filtering (multi-tag AND, result list to card) | done (MVP) |
| 4 | AI categorization (batch LLM taxonomy into `ai_category`) | planned |
| 5 | Smarter search (semantic KNN; local query embeddings, hybrid with FTS) | in progress |
| 6 | Statistics and taxonomy UI (hierarchy, counts, "study this bucket") | planned |
