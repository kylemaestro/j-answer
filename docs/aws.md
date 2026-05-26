# Deploy j-answer on AWS (EC2)

This is the **streamlined** deploy: run one CloudFormation stack, upload your SQLite database, and run one bootstrap script on the instance. Everything else — packages, repo clone, swap, venv, frontend build, systemd, nginx, Let's Encrypt — is automated.

**Target:** `t4g.small` (2 GB RAM) on Amazon Linux 2023, ARM, behind an Elastic IP, with **nginx** reverse-proxying `/api` to FastAPI/Uvicorn on `127.0.0.1:8000`, the SPA from `web/dist`, the SQLite DB at `/opt/j-answer/data/j-answer.db`, and an `OPENAI_API_KEY` in `/etc/janswer.env` for Magic search.

**Why `t4g.small`, not `t4g.micro`:** Magic search runs a flat (brute-force) KNN over a ~1 GB sqlite-vec index. On `t4g.micro` (1 GB RAM) that index doesn't fit in the OS page cache, so every Magic query re-reads it from EBS and times out. `t4g.small` (2 GB RAM) gives enough page cache to keep the index warm; Magic settles at ~1–2 s per query, dominated by the OpenAI embed call. See **Performance notes** at the bottom for the full reasoning.

**What you get at the end:** HTTPS site at `https://<your-domain>` with SPA at `/`, API at `/api/*`, Magic search working, idempotent re-deploys via re-running `bootstrap.sh`.

---

## TL;DR

**On your laptop** — one config file, one script for CloudFormation:

```powershell
# Windows PowerShell (from repo root)
Copy-Item deploy\deploy-stack.env.example deploy\deploy-stack.env
notepad deploy\deploy-stack.env   # set DOMAIN, paths, optional KEY_NAME
.\deploy\deploy-stack.ps1
```

```bash
# macOS / Linux / Git Bash / WSL (from repo root)
cp deploy/deploy-stack.env.example deploy/deploy-stack.env
# edit deploy/deploy-stack.env
bash deploy/deploy-stack.sh
```

The script deploys the stack, prints **PublicIp**, and lists the remaining steps. It does **not** run bootstrap on the server (that needs your OpenAI key and DNS).

**Still manual after the script:**

1. Point DNS for your domain at **PublicIp** (GoDaddy or Route 53).
2. Upload the database (or set `UPLOAD_DB=true` in `deploy-stack.env` and re-run the script once SSH works):

   ```powershell
   scp -i $env:USERPROFILE\.ssh\id_ed25519 .\j-answer.db ec2-user@<PublicIp>:/opt/j-answer/data/j-answer.db
   ```

3. On the instance (Session Manager or SSH), run bootstrap once:

   ```bash
   sudo /opt/j-answer/app/deploy/bootstrap.sh --domain j-answer.kylemeister.dev --email you@example.com
   ```

Config lives in `deploy/deploy-stack.env` (gitignored). See `deploy/deploy-stack.env.example` for all knobs.

**Why not one script for everything?** Bootstrap and certbot must run **on the EC2 host** with DNS already pointing at the box; the OpenAI key should not be pasted into a laptop script you might commit. Splitting **laptop = stack + optional scp** vs **server = bootstrap** keeps secrets and ordering safe.

That's it. The rest of this document is the long-form version.

---

## 1. Prerequisites

- AWS account and [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured (`aws sts get-caller-identity` works).
- DNS access for `kylemeister.dev` (GoDaddy or your registrar). You'll add one **A** record for the `j-answer` host after the stack exposes its Elastic IP.
- Your SQLite database file (`j-answer.db`) on your laptop — you'll `scp` it to the instance.
- An **OpenAI API key** with access to `text-embedding-3-small` (used only at query time; clue embeddings are precomputed locally).
- A default VPC in the target region (or pass an explicit `VpcId` and `SubnetId`).

---

## 2. Deploy the stack (CloudFormation)

From the repository root, use the deploy script (recommended):

| Shell | Command |
| --- | --- |
| **Windows PowerShell** | `Copy-Item deploy\deploy-stack.env.example deploy\deploy-stack.env` then `.\deploy\deploy-stack.ps1` |
| **Git Bash / WSL / macOS / Linux** | `cp deploy/deploy-stack.env.example deploy/deploy-stack.env` then `bash deploy/deploy-stack.sh` |

Edit `deploy/deploy-stack.env` first (`AWS_REGION`, `DOMAIN`, `DB_PATH`, `SSH_KEY_PATH`, optional `KEY_NAME`, `HOSTED_ZONE_ID`). The script resolves the default VPC, runs `aws cloudformation deploy`, and prints stack outputs.

**PowerShell note:** `export VAR=value` is bash-only. On Windows use `$env:AWS_REGION = "us-east-1"` for ad-hoc vars, or put values in `deploy-stack.env` and let the script load them.

<details>
<summary>Manual equivalent (if you prefer raw AWS CLI)</summary>

```bash
export AWS_REGION=us-east-1
DEFAULT_VPC=$(aws ec2 describe-vpcs --region "$AWS_REGION" \
  --filters Name=isDefault,Values=true --query "Vpcs[0].VpcId" --output text)

aws cloudformation deploy \
  --region "$AWS_REGION" \
  --stack-name j-answer-app \
  --template-file infra/cloudformation/ec2-janswer.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides VpcId="$DEFAULT_VPC"
```

</details>

**Parameters worth knowing:**

| Parameter | Default | Notes |
| --- | --- | --- |
| `VpcId` | _(required)_ | Default VPC ID. |
| `InstanceType` | `t4g.small` | Don't drop to `t4g.micro` — Magic search will time out (see **Performance notes**). |
| `RootVolumeSizeGiB` | `16` | Holds AL2023 + the ~3.7 GB DB + venv + node_modules + 1 GB swap with room to grow. |
| `RepoUrl` | `https://github.com/kylemaestro/j-answer.git` | Cloned during UserData into `/opt/j-answer/app`. Override for a fork. |
| `RepoRef` | `master` | Branch/tag to check out after clone (must exist on GitHub). |
| `HostedZoneId` | _(empty)_ | Optional — if `kylemeister.dev` is in Route 53 in **this** account, set this and CloudFormation auto-creates the `j-answer` A record. Otherwise add it at GoDaddy (§3). |
| `DnsRecordName` | `j-answer.kylemeister.dev` | FQDN used both for the Route 53 record (when enabled) and as the default `--domain` value for bootstrap. |
| `AllowSSHFromInternet` | `true` | Opens port 22 so you can `scp` the database. Flip to `false` once you've finished bootstrapping and prefer SSM-only. |
| `KeyName` | _(empty)_ | If you have an EC2 key pair in this region, you can pass it and skip the "Install your SSH key" step. |

**Outputs to grab:** `PublicIp`, `InstanceId`, plus the hint outputs `ScpDbHint` and `BootstrapHint`:

```bash
aws cloudformation describe-stacks --region "$AWS_REGION" \
  --stack-name j-answer-app --query "Stacks[0].Outputs" --output table
```

**If a previous attempt failed** (stack stuck in `ROLLBACK_COMPLETE`):

```bash
aws cloudformation delete-stack --region "$AWS_REGION" --stack-name j-answer-app
aws cloudformation wait stack-delete-complete --region "$AWS_REGION" --stack-name j-answer-app
```

Then redeploy.

**What UserData does on first boot** (you don't run any of this — it just happens; check progress with `sudo tail -f /var/log/janswer-userdata.log` on the instance):

1. `dnf update -y` and install: `nginx`, `python3`, `python3-pip`, `git`, `certbot`, `python3-certbot-nginx`, `nodejs20`.
2. Create a **1 GiB swapfile** at `/swapfile` and add it to `/etc/fstab`.
3. Create `/opt/j-answer/{app,data,web/dist}` owned by `ec2-user`.
4. `git clone --branch <RepoRef> <RepoUrl> /opt/j-answer/app`.
5. `systemctl enable --now nginx` (default site; the bootstrap script will replace the config).

Wait until `/var/log/janswer-userdata.log` ends with `UserData done.` (typically 1–3 minutes) before continuing.

---

## 3. DNS at GoDaddy (`j-answer.kylemeister.dev`)

Skip this section if you set `HostedZoneId` in §2 — CloudFormation already created the Route 53 record.

1. In GoDaddy: **My Products** → your domain → **DNS** (DNS Management).
2. **Add** a new record:
   - **Type:** `A`
   - **Name:** `j-answer`
   - **Value / Data:** the stack output `PublicIp`.
   - **TTL:** 1 hour.

Don't change the apex `@` record unless you're moving the root site. Wait until the name resolves from your laptop before running certbot (the bootstrap script will run it for you):

```bash
nslookup j-answer.kylemeister.dev
```

---

## 4. Upload the database

The bootstrap script will refuse to declare success without `/opt/j-answer/data/j-answer.db` in place. Get it on the box with `scp`.

`scp` needs SSH key auth. If you passed `KeyName=...` in §2 it already works. If not, install your laptop's public key via **Session Manager** once:

1. Open **EC2 → Instances → your instance → Connect → Session Manager**. You'll likely land as `ssm-user`.
2. Run:

   ```bash
   sudo mkdir -p /home/ec2-user/.ssh
   sudo chmod 700 /home/ec2-user/.ssh
   echo 'ssh-ed25519 AAAA...your-public-key... me@laptop' | sudo tee -a /home/ec2-user/.ssh/authorized_keys
   sudo chmod 600 /home/ec2-user/.ssh/authorized_keys
   sudo chown -R ec2-user:ec2-user /home/ec2-user/.ssh
   ```

3. Back on your laptop:

   ```powershell
   scp -i $env:USERPROFILE\.ssh\id_ed25519 .\j-answer.db ec2-user@<PublicIp>:/opt/j-answer/data/j-answer.db
   ```

   (Bash equivalent: `scp -i ~/.ssh/id_ed25519 ./j-answer.db ec2-user@<PublicIp>:/opt/j-answer/data/j-answer.db`.)

The DB is ~3–4 GB, so the upload takes a few minutes over a typical home connection. While that's running, kick off DNS (§3) if you haven't yet.

---

## 5. Run the bootstrap script

SSM into the instance one more time and run:

```bash
sudo /opt/j-answer/app/deploy/bootstrap.sh \
  --domain j-answer.kylemeister.dev \
  --email  you@example.com
```

The script prompts (with hidden input) for the OpenAI API key the first time, then writes it to `/etc/janswer.env` (mode `0640`, `root:ec2-user`). You can also pass it non-interactively:

```bash
sudo OPENAI_API_KEY=sk-... /opt/j-answer/app/deploy/bootstrap.sh \
  --domain j-answer.kylemeister.dev --email you@example.com
# — or —
sudo /opt/j-answer/app/deploy/bootstrap.sh \
  --domain j-answer.kylemeister.dev --email you@example.com --openai-key sk-...
```

The script in order:

1. `git pull` the repo (idempotent; no-op on a fresh clone).
2. Create the Python venv and `pip install -r requirements.txt`.
3. `npm ci && npm run build` and copy `web/dist` to `/opt/j-answer/web/dist`.
4. Write `/etc/janswer.env` with the OpenAI key, `JANSWER_DB`, and `CORS_ORIGINS`.
5. Install the systemd unit (`/etc/systemd/system/janswer-api.service`).
6. Install the nginx site (`/etc/nginx/conf.d/janswer.conf`) with your `--domain` substituted in.
7. Run `certbot --nginx -d <domain>` for TLS (skip with `--skip-tls`, or skip implicitly by omitting `--domain`).
8. Restart `janswer-api` and verify `/api/health` and `/api/embeddings/status` return successfully.

If certbot complains about DNS resolution, your DNS hasn't propagated yet. Re-run the script later — it picks up where it left off:

```bash
sudo /opt/j-answer/app/deploy/bootstrap.sh \
  --domain j-answer.kylemeister.dev --email you@example.com
```

The first **Magic** search after a deploy still has to pull ~1 GB of vec index off EBS — the FastAPI `lifespan` hook kicks that off in a background thread on startup, so by the time you actually open the site it should be hot. If you immediately hit the site within ~10 s of restart you might catch the warmup; just retry.

---

## 6. Routine updates

After you push to `master`:

```bash
sudo /opt/j-answer/app/deploy/bootstrap.sh --domain j-answer.kylemeister.dev
```

Same script, same flags. It will `git pull`, rebuild the frontend, reinstall Python deps if `requirements.txt` changed, restart Uvicorn, and re-check health. The env file and certificate are left alone if they already exist.

For Python-only changes (no `web/` touches) skip the frontend build:

```bash
sudo /opt/j-answer/app/deploy/bootstrap.sh --domain j-answer.kylemeister.dev --skip-frontend
```

**Updating the SQLite database later:**

```bash
sudo systemctl stop janswer-api
# upload new j-answer.db over the existing one (scp, SFTP, S3, etc.)
sudo chown ec2-user:ec2-user /opt/j-answer/data/j-answer.db
sudo systemctl start janswer-api   # the lifespan hook pre-warms the new index
```

---

## 7. Troubleshooting

**`bootstrap.sh: command not found` or `/opt/j-answer/app/deploy/bootstrap.sh` missing.** UserData clones `RepoRef` (default **`master`** — not `main`). If the stack was deployed with `RepoRef=main` but GitHub only has `master`, the clone failed and the app tree is empty. On the instance:

```bash
sudo tail -n 30 /var/log/janswer-userdata.log
ls -la /opt/j-answer/app/deploy/bootstrap.sh   # should exist

# Fix: clone or sync master
sudo rm -rf /opt/j-answer/app
sudo -u ec2-user git clone --branch master https://github.com/kylemaestro/j-answer.git /opt/j-answer/app
sudo chmod +x /opt/j-answer/app/deploy/bootstrap.sh
```

If `/opt/j-answer/app` is already a git repo but behind:

```bash
sudo -u ec2-user git -C /opt/j-answer/app fetch origin master
sudo -u ec2-user git -C /opt/j-answer/app checkout master
sudo -u ec2-user git -C /opt/j-answer/app pull --ff-only
sudo chmod +x /opt/j-answer/app/deploy/bootstrap.sh
```

Then run bootstrap again (§5).

**UserData didn't finish / `/opt/j-answer/app` is missing.** Inspect `/var/log/janswer-userdata.log` (also written to `/var/log/cloud-init-output.log`). Common causes: GitHub clone failed (wrong `RepoRef`, typo in `RepoUrl`, private repo without auth), or `dnf install nodejs20` failed on an older AMI snapshot. You can fix the root cause and rerun manually:

```bash
sudo dnf install -y nginx python3 python3-pip git certbot python3-certbot-nginx nodejs20
sudo -u ec2-user git clone --branch master https://github.com/kylemaestro/j-answer.git /opt/j-answer/app
```

**`502 Bad Gateway` from nginx on `/api/*`.** Means Uvicorn isn't answering on `127.0.0.1:8000`. Inspect:

```bash
sudo systemctl status janswer-api --no-pager
sudo journalctl -u janswer-api -n 80 --no-pager
curl -v http://127.0.0.1:8000/api/health   # bypasses nginx
```

If the unit is failing to start, the most common causes are (1) missing `OPENAI_API_KEY` in `/etc/janswer.env`, (2) DB file unreadable by `ec2-user` (check `ls -la /opt/j-answer/data/j-answer.db`), or (3) a syntax error in `src/api_app.py` after a deploy. Fix and `sudo systemctl restart janswer-api`.

**`503` "Magic search index is missing or empty"** from `/api/search/magic`. The `clue_vec_index` table isn't populated. Build it:

```bash
sudo -u ec2-user /opt/j-answer/app/venv/bin/python \
  /opt/j-answer/app/scripts/migrate_vec_index.py --db /opt/j-answer/data/j-answer.db
sudo systemctl restart janswer-api
```

**Magic search is slow on the first try after deploy.** Expected — the index is cold. The lifespan hook pre-warms in the background, but if you hit `/api/search/magic` within ~10 s of restart you'll wait for the EBS read. Subsequent queries should be ~1–2 s.

**Magic search is slow on every try.** Most likely RAM pressure. Confirm with:

```bash
free -m          # available memory; should be > 300 MB at rest
top -b -n 1 | head -n 20
```

If `available` is consistently < 200 MB, something else on the box is eating cache (a runaway scraper, lots of concurrent traffic, etc.). Either restart `janswer-api` to reset Python's resident set or upgrade to `t4g.medium`.

**Certbot renewal.** AL2023's `certbot` package installs a systemd timer; renewals are automatic. Confirm with `systemctl list-timers | grep certbot`.

---

## 8. Performance notes (why `t4g.small`?)

The Magic search hot path is:

1. Embed the query via OpenAI (`text-embedding-3-small`, 512 dims) — ~0.3–1.5 s network round-trip.
2. Run a flat (brute-force) cosine KNN over `clue_vec_index` — ~500k × 512 floats = **~1 GB** of vector data scanned per query.
3. Look up matching `clues` rows by id.

On `t4g.micro` (1 GB RAM), once you subtract the kernel (~150 MB), nginx (~30 MB), and the Python process (~150 MB), you're left with ~600 MB of OS page cache to hold a ~1 GB working set. Every Magic query evicts pages it just loaded. With burstable CPU credits draining and EBS reads competing for I/O bandwidth, queries balloon past nginx's 120 s `proxy_read_timeout`. That's the timeout problem.

On `t4g.small` (2 GB RAM), the index fits in page cache with comfortable headroom. After the first query (which the `lifespan` pre-warm absorbs), every subsequent Magic call is memory-bandwidth-bound (~100–500 ms for the KNN) plus the OpenAI round-trip. No more timeouts.

If you outgrow even `t4g.small` (multiple concurrent users hammering Magic), the next steps in order are:

1. **`t4g.medium`** (4 GB) — same shape, more concurrency headroom.
2. **Local query embeddings** — drop the OpenAI round-trip by embedding queries with a small sentence-transformer. Requires re-embedding every clue with the same model. See `README.md` roadmap.
3. **Move KNN off the box** — host vectors in a managed service (Qdrant, Pinecone, Supabase pgvector). The EC2 instance becomes stateless.

---

## 9. Changing the public hostname later (e.g. `j-answer.com`)

Same three ideas as the original setup: **DNS → nginx `server_name` → TLS certificate**.

1. **DNS:** add an A record for the new name pointing at the same Elastic IP.
2. **Re-run bootstrap with the new domain:** `sudo /opt/j-answer/app/deploy/bootstrap.sh --domain j-answer.com --email you@example.com`. The script will rewrite nginx config and request a new cert. If you want both names served at once, edit `/etc/nginx/conf.d/janswer.conf` after bootstrap to list both in `server_name`, then `sudo certbot --nginx -d j-answer.kylemeister.dev -d j-answer.com` to expand the existing cert.

The `CORS_ORIGINS` value in `/etc/janswer.env` is also updated by the bootstrap script. If you serve the SPA and API on the same origin (recommended; this is what the nginx config does), CORS rarely matters.

---

## 10. Stack layout (reference)

| Piece | Role |
| ----- | ---- |
| **CloudFormation** | EC2 (`t4g.small`, 16 GiB gp3), Elastic IP, security group (80/443 + optional 22), IAM profile with **SSM**; optional Route 53 `A` record. UserData clones the repo, installs base packages, and creates a 1 GiB swapfile. |
| **`deploy/bootstrap.sh`** | One-shot, idempotent app-level setup. Venv, frontend build, env file, systemd, nginx, certbot, health checks. |
| **DNS (typical)** | GoDaddy: `A j-answer` → Elastic IP; apex `@` unchanged for the portfolio. |
| **`/etc/janswer.env`** | `OPENAI_API_KEY`, `JANSWER_DB`, `CORS_ORIGINS`. Mode `0640`, `root:ec2-user`. Read by the systemd unit via `EnvironmentFile=`. |
| **nginx** | Serves `web/dist`, proxies `/api/` to Uvicorn on `127.0.0.1:8000`. Long timeouts so Magic search has room to breathe. |
| **systemd (`janswer-api`)** | Runs Uvicorn as `ec2-user`. Pre-warms the vec index on startup. |
| **SQLite** | `/opt/j-answer/data/j-answer.db`. WAL mode, mmap-friendly. |

Template source: `infra/cloudformation/ec2-janswer.yaml`. Example configs: `deploy/nginx-janswer.conf.example`, `deploy/janswer-api.service.example`. Bootstrap: `deploy/bootstrap.sh`.
