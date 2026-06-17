# Database and schema

j-answer stores everything in a single SQLite file (default `j-answer.db` in the
repository root; override with `--db` on the CLI or `JANSWER_DB` for the API).

The schema is created on first run by `init_schema` in `src/db.py`. SQLite runs
in WAL mode and is mmap-friendly.

## Tables

| Table | Purpose |
| ----- | ------- |
| `clues` | One row per clue: category, clue text, answer, round, value, air date, J-Archive ids. |
| `clues_fts` | FTS5 full-text index over clue / answer / category, kept in sync with `clues` via triggers. Backs Exact search. |
| `crawl_games` | Resumable scrape queue (schema version 2): one row per `game_id` with a status (`pending` / `failed` / `complete`). |
| `clue_embeddings` | Optional. One row per embedded clue: `clue_id` -> float32 embedding BLOB. Source of truth for semantic search and for reranking. |
| `clue_vec_index` | Optional. [sqlite-vec](https://github.com/asg017/sqlite-vec) `vec0` virtual table holding the binary-quantized index used for the KNN candidate scan. |
| `meta` | Key/value bookkeeping (e.g. the vec-index schema version). |

`clue_embeddings` and `clue_vec_index` are only present after you run the
embedding job. See `docs/search.md` for how they are built and used.

## Embedding BLOB format

The on-disk contract (must match `embed/vector-embed.py` and the search code;
defined in `src/embeddings.py`):

- Model: `text-embedding-3-small` (OpenAI)
- Dimensions: 512 (requested at embed time, not the model default 1536)
- Stored type: numpy float32, little-endian
- On disk: raw bytes in the `embedding` BLOB, no header (length = 512 * 4 = 2048)
- Text embedded per clue: `"{game_category} | {clue_text} | {answer_text}"`

Query vectors must use the same model and dimensions for cosine similarity to be
meaningful.

The binary-quantized copy in `clue_vec_index` is derived from these float blobs
(one sign bit per dimension, 64 bytes per clue). It is never the source of
truth; it can always be rebuilt from `clue_embeddings`.
