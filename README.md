# j-answer

“Don’t put your future in Jeopardy!”

Scrape [J-Archive](https://j-archive.com) into SQLite, with an optional web UI for random flashcards and multi-tag full-text search.

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

Endpoints used by the UI:

| Method | Path | Purpose |
| ------ | ---- | ------- |
| `GET` | `/api/health` | Liveness check |
| `GET` | `/api/random-clue` | One random clue |
| `GET` | `/api/search?tag=a&tag=b` | Full-text search; repeat `tag` for each term (AND across clue text, answer, and in-game category). Optional `limit` (1–500, default 100). |

**CORS:** Local Vite origins are always allowed. For other browser origins, set **`CORS_ORIGINS`** (comma-separated, no spaces required) in the API environment, e.g. `https://j-answer.kylemeister.dev`. Same-origin nginx + `/api` proxy usually does not require CORS changes.

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

Static output is in `web/dist/`. For production, prefer **same-origin** nginx: serve `web/dist` and **reverse-proxy** `/api` to Uvicorn (see **Deployment and infrastructure** and `deploy/nginx-janswer.conf.example`).

### Using the UI

- **Search** — type a word or phrase, press Enter or **Add tag**; each tag appears as a removable badge. All tags apply together (**AND**) via SQLite FTS5 over clue text, correct response, and category. Tap a result row to open that clue on the card.
- **I’m feeling lucky** — loads a random clue from the database.
- **Card** — click or tap to flip between clue and answer (keyboard: Enter / Space when focused).

---

## Deployment and infrastructure

This section describes a **lightweight, low-traffic** setup: one **EC2** instance (same pattern works on **Lightsail** with the same nginx + systemd layout), subdomain **`j-answer.kylemeister.dev`**, **Let’s Encrypt** TLS, and **GitHub Actions** pushing builds over **SSH**. You can change the public hostname later by updating **DNS**, **nginx `server_name`**, **certbot**, and **`CORS_ORIGINS`** (only needed if the browser origin differs from the API origin).

### Architecture

| Piece | Role |
| ----- | ---- |
| **Route 53** | `A` record (or `AAAA`) for `j-answer.kylemeister.dev` → instance **Elastic IP** (or Lightsail static IP). |
| **EC2** (or Lightsail) | **nginx** serves `web/dist` and reverse-proxies `/api/` → **Uvicorn** on `127.0.0.1:8000`. |
| **SQLite** | File on disk, e.g. `/opt/j-answer/data/j-answer.db`, referenced by **`JANSWER_DB`**. |
| **GitHub Actions** | On push to `main`: build the SPA, **rsync** `web/dist/` + app tree to the server, `pip install`, **`systemctl restart janswer-api`**. |

The SPA calls **`/api/...`** on the **same host**, so you normally **do not** need a separate `VITE_API_BASE` when nginx proxies as in `deploy/nginx-janswer.conf.example`.

### 1. AWS: stack (CloudFormation) or manual EC2

**Option A — CloudFormation (recommended IaC)**  
See **`infra/README.md`** and template **`infra/cloudformation/ec2-janswer.yaml`**. It creates a small **Amazon Linux 2023** instance, **Elastic IP**, security group (**80/443**; optional **22** for SSH-based deploys), and an **IAM instance profile** with **SSM** (`AmazonSSSManagedInstanceCore`) so you can always use **Session Manager** even if you later close port 22.

- Match **architecture** to **`InstanceType`**: default AMI is **ARM64** (`t4g.*`). For **x86** (`t3.micro`), override the **`LatestAmiId`** parameter to  
  `/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64` (see parameter description in the template).
- **`AllowSSHFromInternet=true`** opens **SSH to the world** — convenient for **GitHub-hosted runners** + `rsync`; for stricter setups, set it to **`false`** after you adopt **S3 artifact + SSM Run Command** or a **self-hosted runner** in your VPC.

**Option B — Manual**  
Launch any small instance (e.g. **t4g.micro** / **t3.micro**), attach a **static/EIP**, attach a security group allowing **80** and **443** (and **22** only if you use SSH deploy).

### 2. First boot on the server (once)

Paths below match the examples; adjust users/paths if you use Lightsail images other than AL2023.

```bash
sudo mkdir -p /opt/j-answer/{app,data,web/dist}
sudo chown -R "$USER:$USER" /opt/j-answer
cd /opt/j-answer/app
python3 -m venv venv
./venv/bin/pip install --upgrade pip
# After first clone/rsync of requirements.txt + src + janswer:
./venv/bin/pip install -r requirements.txt
```

Install **nginx**, copy **`deploy/nginx-janswer.conf.example`** to e.g. `/etc/nginx/conf.d/janswer.conf`, edit `server_name` if needed, then:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

Install **Certbot** (nginx plugin) and issue a cert for your hostname, e.g.:

```bash
sudo dnf install -y certbot python3-certbot-nginx   # AL2023
sudo certbot --nginx -d j-answer.kylemeister.dev
```

Install **systemd** unit from **`deploy/janswer-api.service.example`** → `/etc/systemd/system/janswer-api.service`. Edit **`Environment=CORS_ORIGINS=...`** if the site is ever served from a different browser origin than the API (same host + `/api` proxy → you can omit or set to your `https://` URL). Set **`Environment=JANSWER_DB=...`** to your DB path. Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now janswer-api
```

### 3. DNS

In **Route 53**, create an **`A` record** for `j-answer.kylemeister.dev` pointing to the instance **Elastic IP** (or Lightsail static IP). Wait for propagation before running **certbot**.

### 4. Changing the URL later

1. Add DNS for the new name (or repoint the existing record).  
2. Update **nginx** `server_name` and reload; run **certbot** for the new hostname.  
3. If the browser origin changes (e.g. static site on another domain), set **`CORS_ORIGINS`** on the API (comma-separated list) to include the new `https://...` origin.

### 5. Updating the SQLite database manually

The API reads **`JANSWER_DB`** (default in code is repo-relative `j-answer.db`; on the server use e.g. **`/opt/j-answer/data/j-answer.db`** in **systemd**).

**Typical flow:**

1. Build or copy a new **`j-answer.db`** locally (scraper on your machine is fine).  
2. **Stop** the API briefly (optional but avoids rare locks):  
   `sudo systemctl stop janswer-api`  
3. **Copy** the file to the server, e.g.  
   `scp -i ~/.ssh/your_key ./j-answer.db ec2-user@YOUR_IP:/opt/j-answer/data/j-answer.db`  
4. Ensure ownership if needed:  
   `sudo chown ec2-user:ec2-user /opt/j-answer/data/j-answer.db`  
5. **Start** the API:  
   `sudo systemctl start janswer-api`

No automation is required; repeat whenever you refresh metadata or rescrape.

### 6. GitHub Actions auto-deploy

Workflow: **`.github/workflows/deploy-ec2.yml`** (runs on push to **`main`**).

**Repository secrets** (Settings → Secrets and variables → Actions):

| Secret | Meaning |
| ------ | ------- |
| `DEPLOY_HOST` | Public IP or DNS of the instance (e.g. Elastic IP). |
| `DEPLOY_USER` | SSH user (e.g. `ec2-user` on AL2023). |
| `DEPLOY_SSH_KEY` | Private key for that user (use a **deploy-only** key added to `~/.ssh/authorized_keys` on the server). |

The job runs **`npm ci`** / **`npm run build`** in **`web/`**, then **rsync**s **`janswer/`**, **`src/`**, **`requirements.txt`**, and **`web/dist/`** into **`/opt/j-answer/`** on the server, runs **`pip install -r requirements.txt`**, and **`sudo systemctl restart janswer-api`**.

**Security note:** GitHub-hosted runners use **dynamic egress IPs**, so **SSH cannot be locked to “GitHub only”** without a **self-hosted runner**, **VPN**, or switching deploy to **S3 + SSM** (no public 22). Practical choices: (a) keep **port 22** restricted as tightly as you can after first setup, use **key-only** auth, consider **fail2ban**; or (b) set **`AllowSSHFromInternet=false`** in CloudFormation and deploy via **SSM**/**S3** (document your own small script using IAM OIDC — not wired in this repo by default).

**OIDC to AWS** (no long-lived `AWS_ACCESS_KEY_ID` in GitHub) pairs naturally with **S3 + SSM**; the **SSH + secrets** path above is the **smallest** end-to-end story for a single hobby instance.

### 7. Relationship to your existing Lightsail / Apache site

`kylemeister.dev` can keep pointing at your **current Lightsail** stack (**Apache** + Let’s Encrypt). **`j-answer.kylemeister.dev`** can be a **separate `A` record** to a **dedicated small EC2** (this repo’s intended layout), so you do not have to merge vhosts into Apache unless you want to. If you later prefer **one** server, you can **reverse-proxy** from Apache to this app or consolidate onto nginx only—same app paths and env vars still apply.

---

## Roadmap

End goal: a web-based, Quizlet-style experience over the full J-Archive-derived corpus, with strong search, AI-assisted taxonomy, and analytics.

| Phase | Scope | Direction |
| ----- | ----- | --------- |
| **1** | Data acquisition & schema | SQLite store, scraper, FTS5 on clue / answer / category — **done** |
| **2** | Core flashcard UI | Random clue, flip animation, Jeopardy-style presentation — **done** |
| **3** | Search & filtering | Multi-tag AND search, result list → card; FTS-backed — **done** for MVP (pagination, highlights, round/date filters later) |
| **4** | AI categorization | Batch LLM pass to fill `ai_category` / `ai_subcategory` from a controlled taxonomy; storage and re-runs |
| **5** | Smarter search | Semantic or embedding-backed search so intent (e.g. “first U.S. presidents”) matches clues without literal phrase overlap |
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
web/                # Vite + React flashcard SPA
deploy/             # nginx + systemd examples for production
infra/              # CloudFormation (EC2 + EIP) and infra README
.github/workflows/ # CI deploy (EC2 over SSH)
requirements.txt
```

`python -m janswer` loads `janswer/__main__.py`, which calls into `src/__main__.py` so the working directory stays the repo root and imports remain stable.
