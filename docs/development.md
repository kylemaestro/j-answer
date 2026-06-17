# Local development

The `web/` app is a Vite + React + Tailwind SPA. It reads clues from SQLite
through `src/api_app.py`, a FastAPI backend. In development the SPA proxies
`/api` to the backend (see `web/vite.config.ts`).

## Prerequisites

- Python deps: `pip install -r requirements.txt` (includes FastAPI, Uvicorn,
  sqlite-vec).
- A populated SQLite file (default `j-answer.db` at the repo root, or set
  `JANSWER_DB`). See `docs/scraping.md`.
- Node.js 20+ and npm for the frontend.

## Environment variables

| Variable | Purpose |
| -------- | ------- |
| `JANSWER_DB` | Path to the SQLite file. Defaults to `j-answer.db` in the repo root. |
| `OPENAI_API_KEY` | Required on the API process for Magic search (used to embed the query). Not read by the browser or Vite. |
| `JANSWER_MAGIC_MIN_SCORE` | Minimum cosine similarity for Magic results (default `0.45`; the UI slider overrides per request). |
| `JANSWER_MAGIC_RERANK_POOL` | Candidate pool size for the Magic KNN before float rerank (default `1000`). Higher = better recall, slightly slower. See `docs/search.md`. |
| `JANSWER_MAGIC_RESULT_LIMIT` | Max clues returned per Magic query (default `100`, max `500`). |
| `CORS_ORIGINS` | Comma-separated extra allowed origins for production. Local Vite origins are always allowed. |

Setting `OPENAI_API_KEY` for the embed job does not carry over to the API
process — set it in the same shell (or run configuration) before starting
Uvicorn, then restart if it was already running. The app does not auto-load a
`.env` file.

## Run the API

```powershell
# Windows PowerShell
$env:OPENAI_API_KEY = "sk-..."
python -m uvicorn src.api_app:app --reload --host 127.0.0.1 --port 8000
```

```bash
# macOS / Linux
export OPENAI_API_KEY="sk-..."
python -m uvicorn src.api_app:app --reload --host 127.0.0.1 --port 8000
```

Drop `--reload` (and optionally add workers) for a production-style run.

Quick check:

```bash
curl "http://127.0.0.1:8000/api/search/magic?q=test"
```

A missing key returns `503` with `OPENAI_API_KEY is not set on the API server`.

## Run the web UI

```bash
cd web
npm install
npm run dev      # dev server, proxies /api to 127.0.0.1:8000
```

Open the URL Vite prints (typically `http://127.0.0.1:5173`).

Production build:

```bash
cd web
npm install
npm run build    # static output in web/dist
```

For production, serve `web/dist` and reverse-proxy `/api` to Uvicorn from the
same origin (nginx). See `docs/aws.md`.

## API endpoints

| Method | Path | Purpose |
| ------ | ---- | ------- |
| `GET` | `/api/health` | Liveness check. |
| `GET` | `/api/random-clue` | One random clue. |
| `GET` | `/api/search?tag=a&tag=b` | Exact FTS5 search; repeat `tag` for each term (AND). Prefix to narrow: `answer:`, `clue:`, `category:`, `year:`. Optional `limit` (1–500, default 100). |
| `GET` | `/api/search/magic?q=...` | Magic semantic search. Requires `OPENAI_API_KEY`. Optional `min_score`; `limit` is retained for compatibility but no longer caps I/O (see `docs/search.md`). |
| `GET` | `/api/embeddings/status` | `embedded`, `vec_indexed`, and `magic_available`. |

## Using the UI

- **Search** — toggle Exact (tags + FTS5) or Magic (natural-language search with
  a confidence slider for minimum similarity). Magic requires embeddings in the
  DB and `OPENAI_API_KEY` on the API. Tap a result row to open that clue.
- **I'm feeling lucky** — loads a random clue.
- **Card** — click/tap to flip between clue and answer (Enter / Space when
  focused).
