# Search and embeddings

j-answer has two search modes over the same SQLite file:

- **Exact** — SQLite FTS5 multi-tag search over `clues_fts` (`src/search.py`).
  Fast, no embeddings required.
- **Magic** — semantic "vibe" search over embedded clues (`src/semantic_search.py`).
  Requires precomputed embeddings and an OpenAI key at query time.

The first half of this document covers building the embeddings; the second half
explains how Magic search works.

---

## Building the embeddings (one-time local job)

Magic search needs OpenAI embeddings precomputed into `clue_embeddings`. This is
a local batch job. Stop the API first if it is using the DB.

### Setup

```bash
pip install -r requirements.txt
pip install -r embed/requirements-embed.txt
```

(`sqlite-vec` is in both; the API needs it for Magic search at runtime.)

Set your API key (never commit it):

```powershell
$env:OPENAI_API_KEY = "sk-..."
```

### Run the embed job

From the repository root (default DB is `j-answer.db` at the root):

```bash
python embed/vector-embed.py
```

Smoke-test on a small batch first:

```bash
python embed/vector-embed.py --limit 100
```

| Flag | Default | Description |
| ---- | ------- | ----------- |
| `--db` | `j-answer.db` | SQLite path |
| `--batch-size` | `1000` | Clues per OpenAI request |
| `--limit` | `0` (no cap) | Max clues to embed this run |
| `--retries` | `5` | Attempts per batch on 429 / 5xx / connection errors |
| `--backoff-base` | `2.0` | Seconds before first retry (doubles each attempt) |
| `-v` | off | Debug logging |

The job is resumable: only clues missing from `clue_embeddings` are processed.
A full archive is on the order of ~550k clues (~1.1 GB of float vectors) plus
OpenAI token charges; use `--limit` to validate first.

### Build the vec index

After embeddings exist (or after copying a DB that only has `clue_embeddings`),
build the binary-quantized `clue_vec_index` once per database. Stop the API
first if it holds the file open.

```bash
python scripts/migrate_vec_index.py --db j-answer.db
```

| Flag | Default | Description |
| ---- | ------- | ----------- |
| `--db` | `j-answer.db` | SQLite path |
| `--batch-size` | `5000` | Rows per upsert batch |
| `-v` | off | Debug logging |

The index is derived entirely from the float blobs in `clue_embeddings` — **no
OpenAI re-embedding is needed** to (re)build it. `vector-embed.py` also updates
the index incrementally when sqlite-vec is installed.

### Rebuilding in production (EC2)

After deploying code that changes the vec-index schema, or after uploading a new
database, rebuild the index on the box. Via AWS Systems Manager:

```bash
aws ssm send-command --region us-east-1 \
  --instance-ids i-0123456789abcdef0 \
  --document-name AWS-RunShellScript \
  --parameters commands='[
    "sudo systemctl stop janswer-api",
    "sudo -u ec2-user bash -lc \"cd /opt/j-answer/app && source venv/bin/activate && python scripts/migrate_vec_index.py --db /opt/j-answer/data/j-answer.db\"",
    "sudo systemctl start janswer-api"
  ]'
```

Confirm the result:

```bash
curl -sS "http://127.0.0.1:8000/api/embeddings/status"
```

`magic_available` should be `true` and `vec_indexed` should equal `embedded`.
See `docs/aws.md` for the full deploy/operate guide.

---

## How Magic search works

### The data: 512-D float embeddings

Each clue is embedded by `text-embedding-3-small` into a 512-dimensional
float32 vector, L2-normalized to unit length, and stored in `clue_embeddings`
(~2 KB/clue). Because every vector has length 1, **cosine similarity is just the
dot product**: "similar meaning" means "points in nearly the same direction" in
512-D space.

A full archive is ~550k clues ≈ 1.1 GB of float vectors.

### Why not brute-force the floats?

The original design ran a flat (brute-force) KNN directly over that ~1.1 GB
float index on every query. On a small EC2 instance (`t4g.small`, 2 GB RAM,
burstable CPU, behind a 3.7 GB DB) that working set does not stay in page cache,
so each query re-read ~1 GB from EBS and blew past nginx's 120 s timeout (504).
The fix was to make the per-query working set small enough to live in RAM.

### Binary quantization (the index)

`clue_vec_index` is a sqlite-vec `vec0` virtual table declared as `bit[512]`.
Each float vector is compressed to one sign bit per dimension:

```
component > 0  -> 1        component <= 0  -> 0
```

512 bits pack into 64 bytes (`embedding_to_bit_blob()` in `src/embeddings.py`),
a 32x shrink: the whole index drops from ~1.1 GB to ~35 MB, which fits in cache.
Example with 8 dims:

```
float:  [ 0.21, -0.08,  0.50, -0.33, -0.02,  0.11,  0.77, -0.40 ]
signs:  [   1,    0,     1,     0,     0,     1,     1,     0   ]  -> 0b10100110
```

This discards each dimension's magnitude (kept for reranking, below) but
preserves direction well enough for a coarse first pass.

### Hamming distance (the metric)

Bit vectors can't be dot-producted, so the index uses Hamming distance: the
number of bit positions where two vectors disagree (`popcount(a XOR b)`).
Vectors pointing in similar directions share signs on most dimensions, so a
small Hamming distance approximates a high cosine similarity. Each comparison is
a handful of CPU instructions over 64 bytes, so scanning all 550k clues takes
~1.7 s on the box.

### Two-stage KNN: coarse retrieve, then exact rerank

A Magic query (`search_clues_by_vibe`) runs in two stages:

```
query text
   |  OpenAI embed -> 512-D float  ------------------------------+
   |                                                             | (kept for stage 2)
   v binarize (sign bits -> 64 B)                                |
+--------------------------------------------+                   |
| STAGE 1: Hamming KNN over ALL 550k bits     |                   |
| (~35 MB index, ~1.7 s, approximate)         |                   |
+--------------------------------------------+                   |
   | top N clue_ids  (N = JANSWER_MAGIC_RERANK_POOL, default 1000)|
   v                                                             v
+--------------------------------------------+         fetch N float vecs
| STAGE 2: exact cosine rerank on N candidates|<--------  by id (~2 MB)
| filter by min_score, return top 100         |
+--------------------------------------------+
   |
   v final ranked results
```

1. **Embed** the query via OpenAI (512-D float), then **binarize** it the same
   way as the index. Identical packing on both sides is what makes the Hamming
   numbers comparable.
2. **Stage 1 (coarse):** flat Hamming KNN (`MATCH vec_bit(?) AND k = ?`) returns
   the closest N candidates out of 550k. N is `JANSWER_MAGIC_RERANK_POOL`
   (default 1000).
3. **Stage 2 (exact):** fetch those N clues' original float32 vectors by primary
   key from `clue_embeddings` (~2 MB, a bounded indexed read), compute the real
   cosine similarity, filter by `min_score`, sort, return the top 100.

The bit index does the fast, rough "find ~1000 plausible matches out of 550k";
the float vectors do the accurate "rank these 1000 properly." Expensive precise
math only ever runs on 1000 vectors, never 550k.

### Why quality stays high

Quantization only affects which candidates get shortlisted, not their final
scores (the rerank uses full-precision floats). As long as the true best matches
land in the candidate pool — they almost always do, since the bit ranking is
highly correlated with the real one — the exact rerank recovers the correct
order. Measured recall vs. an exact full scan: ~0.99 at top-10, ~0.96 at
top-100. Raise `JANSWER_MAGIC_RERANK_POOL` to trade a little latency for higher
recall. This is the standard coarse-to-fine / quantize-and-rerank pattern.

### Latency notes

A steady-state Magic query in production is ~2.8 s, split between the ~1.7 s
Hamming scan (CPU-bound brute force on the burstable ARM core) and the OpenAI
embed round-trip, plus rerank and overhead.

- **No `COUNT(*)` on the hot path.** `SELECT COUNT(*) FROM clue_embeddings` is a
  ~13 s full-table scan on EBS. The search path uses `count_vec_index()` (fast —
  vec0 keeps a small shadow table) for the pool size and a `LIMIT 1` existence
  check. `/api/embeddings/status` uses a `(size, mtime)`-cached blob count
  primed by the startup prewarm.
- **Cold start.** The first query after deploy/reboot pays the EBS read to pull
  the ~35 MB bit index into cache. The FastAPI `lifespan` hook pre-warms it (and
  primes the count cache) in a background thread, so real users normally hit a
  warm index.

To go faster without new infrastructure, the next lever is **local query
embeddings**: embed queries with a small local model to drop the OpenAI
round-trip (requires re-embedding clues with the same model). See the roadmap in
`README.md`.

## Relevant code

| File | Role |
| ---- | ---- |
| `src/embeddings.py` | `clue_embeddings` BLOB format, float<->bit packing |
| `src/vec_index.py` | `clue_vec_index` (vec0 `bit[512]`) build and sync |
| `src/semantic_search.py` | Magic search: hamming KNN + float rerank, count caching, prewarm |
| `src/search.py` | Exact FTS5 tag search |
| `scripts/migrate_vec_index.py` | One-shot rebuild of `clue_vec_index` from blobs |
| `embed/vector-embed.py` | Local batch embed job (OpenAI) |
