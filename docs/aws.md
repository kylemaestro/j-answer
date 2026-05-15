# Deploy j-answer on AWS (EC2)

This guide walks through one **concrete** setup: a small **Amazon Linux 2023** EC2 instance with an **Elastic IP**, **nginx** (reverse proxy + static files), **Let’s Encrypt** TLS, **FastAPI** behind systemd, and **SQLite** on disk. DNS for **`j-answer.kylemeister.dev`** is added at your **registrar** (this doc assumes **GoDaddy** DNS for `kylemeister.dev`, with the apex **`@`** `A` record already pointing at your portfolio host).

**Why nginx:** It ships in AL2023 with a single package install, works cleanly with **Certbot’s nginx plugin**, and matches the example config in this repo. You do not need Apache on the EC2 box; your portfolio stays on its current host—the app only needs a **new** subdomain record pointing at the instance.

**Optional:** If you later move DNS to **Route 53**, you can pass **`HostedZoneId`** into CloudFormation so the stack creates the **`j-answer`** `A` record for you (see §2 notes).

**What you get at the end:** HTTPS site at `https://j-answer.kylemeister.dev`, SPA from `web/dist`, `/api` proxied to Uvicorn on localhost, database at `/opt/j-answer/data/j-answer.db` (replace by upload when you refresh data).

---

## 1. Prerequisites

- AWS account and [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured (`aws sts get-caller-identity` works).
- Ability to edit **DNS records** for `kylemeister.dev` in **GoDaddy** (or your registrar’s equivalent): you will add one **`A`** record for the **`j-answer`** host after the stack exposes an Elastic IP.
- A **GitHub** (or other Git) remote you can **`git clone`** on the instance—**HTTPS** is fine for public repos; **private** repos need a [personal access token](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens) in the clone URL, **SSH deploy keys**, or **GitHub’s fine-grained token** (this doc does not store secrets in the template).
- Optional: **EC2 key pair** in your target region if you want SSH in addition to **Session Manager**.
- Your AWS account must have a **VPC** for the instance (normally the **default VPC** still exists in each region). The template needs that VPC’s **ID** on deploy (see §2).

---

## 2. Deploy the stack (CloudFormation)

From the **repository root**, pick a region (example: `us-east-1`) and a stack name (example: `j-answer-app`).

**Default (GoDaddy DNS):** do **not** pass `HostedZoneId`. You will point **`j-answer.kylemeister.dev`** at the Elastic IP in GoDaddy (§3).

**VPC ID:** The stack needs **`VpcId`** (the security group is created inside that VPC). For the usual **default VPC**, resolve it once (use the **same** `--region` as deploy):

```bash
export AWS_REGION=us-east-1
DEFAULT_VPC=$(aws ec2 describe-vpcs --region "$AWS_REGION" \
  --filters Name=isDefault,Values=true \
  --query "Vpcs[0].VpcId" --output text)
echo "$DEFAULT_VPC"
```

If that prints `None` or errors, your account may have **no default VPC** in that region—pick a VPC that has a **public subnet** and an **Internet Gateway**, then pass **`SubnetId`** as well (see **Notes** below).

Deploy:

```bash
aws cloudformation deploy \
  --region us-east-1 \
  --stack-name j-answer-app \
  --template-file infra/cloudformation/ec2-janswer.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    VpcId="${DEFAULT_VPC}" \
    InstanceType=t4g.micro \
    AllowSSHFromInternet=true
```

**If a previous attempt failed:** stacks in **`ROLLBACK_COMPLETE`** keep the name until you delete them:

```bash
aws cloudformation delete-stack --region us-east-1 --stack-name j-answer-app
aws cloudformation wait stack-delete-complete --region us-east-1 --stack-name j-answer-app
```

Then run **`deploy`** again with the updated template.

Notes:

- **`VpcId` (required):** Must be the VPC where the instance runs. **`SubnetId`** is optional; leave unset for **default VPC** so EC2 can choose a default subnet. If **`VpcId`** is **not** the default VPC, set **`SubnetId`** to a **public** subnet ID in that VPC (same region).
- **`HostedZoneId` (optional):** Only if **`kylemeister.dev`** is a **Route 53 hosted zone in this AWS account**. When set, CloudFormation creates an **`A`** record for **`DnsRecordName`** (default `j-answer.kylemeister.dev`) → Elastic IP. For **GoDaddy** (or any external DNS), omit this parameter entirely and use §3.
- **`InstanceType`:** Default ARM **`t4g.micro`** is inexpensive and matches the default AL2023 **arm64** AMI. If you use **`t3.micro`** (x86), add AMI override:  
  `--parameter-overrides VpcId="${DEFAULT_VPC}" InstanceType=t3.micro LatestAmiId=/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 AllowSSHFromInternet=true`
- **`AllowSSHFromInternet=true`:** Opens port **22** to the world so you can **SSH** from your laptop or use **GitHub Actions** (that workflow uses **rsync** on a Linux runner, not Windows). For a hobby box this is the simplest path; later you can set it to **`false`** and rely on **Session Manager** only (you will need another way to push files, e.g. S3 + SSM).
- **`KeyName`:** Optional. Add `KeyName=my-keypair` if you created a key pair in that region. You can still use **SSM** without a key.

Confirm outputs (copy **PublicIp** for the next step):

```bash
aws cloudformation describe-stacks \
  --region us-east-1 \
  --stack-name j-answer-app \
  --query "Stacks[0].Outputs" \
  --output table
```

Wait until **`j-answer.kylemeister.dev`** resolves to that IP (GoDaddy + global DNS propagation) **before** running Certbot in §4.5.

---

## 3. DNS at GoDaddy (`j-answer.kylemeister.dev`)

Your **apex** record (**`@`**) can stay as it is today (it keeps **`kylemeister.dev`** and e.g. **`www`** on your portfolio). j-answer is a **separate** hostname on the **same** domain.

1. In GoDaddy: **My Products** → your domain → **DNS** (DNS Management).
2. **Add** a new record:
   - **Type:** `A`
   - **Name:** `j-answer` (GoDaddy expects the subdomain label only; the full name becomes **`j-answer.kylemeister.dev`**). If your UI asks for the full host, use **`j-answer.kylemeister.dev`** per GoDaddy’s help text.
   - **Value / Data:** the stack output **PublicIp** (Elastic IP from §2).
   - **TTL:** 1 hour matches a typical GoDaddy row and is fine.

Do **not** change the existing **`@`** `A` record unless you intend to move the root site; the app only needs the **`j-answer`** row.

Check from your laptop until the name resolves (may take a few minutes to much longer):

```bash
nslookup j-answer.kylemeister.dev
```

---

## 4. First-time server configuration

Connect with **either** method:

- **Session Manager (no SSH key):** EC2 → Instances → select instance → **Connect** → **Session Manager**.
- **SSH:** `ssh ec2-user@<ElasticIP>` (if `KeyName` and security group allow).

Commands below assume **Amazon Linux 2023**. Paths start at the **root of the disk**: use a **leading `/`**, e.g. `cd /opt/j-answer/app` — not `cd opt` (that looks for a folder named `opt` inside your current directory and usually fails).

**Session Manager:** You may land as **`ssm-user`** instead of **`ec2-user`**. **`ec2-user` owns `/opt/j-answer`**, so **`ssm-user` cannot write** there (including **`git clone`** and **`python3 -m venv`**). Use **`sudo -u ec2-user -i`** for an interactive shell, or wrap commands in **`sudo -u ec2-user bash -lc '...'`** as shown below.

**Windows (PowerShell):** `rsync` is usually not installed. This guide uses **`git clone` / `git pull` on the EC2 host** and builds the SPA **on the server** with **`npm`**. For **`j-answer.db`**, **`scp`** (OpenSSH client on Windows) still works from your PC.

### 4.1 Application code (`git clone` on the server)

**What “UserData already created” means:** On **first boot**, AWS ran the template’s **UserData** script (you do not run it yourself). It should have created **`/opt/j-answer/app`**, **`/opt/j-answer/data`**, **`/opt/j-answer/web/dist`**, and set ownership to **`ec2-user`**.

Verify:

```bash
ls -la /opt/j-answer
```

If **`/opt/j-answer`** is **missing**, UserData likely **exited early**. Check:

```bash
sudo tail -n 80 /var/log/cloud-init-output.log
```

A common cause on **AL2023 ARM** is an old template line **`python3-venv`** failing with **`No match for argument`**; with **`set -e`**, later steps never ran. **Recover:** `sudo mkdir -p /opt/j-answer/{app,data,web/dist} && sudo chown -R ec2-user:ec2-user /opt/j-answer` and `sudo dnf install -y nginx python3 python3-pip git` (see current **`infra/cloudformation/ec2-janswer.yaml`**).

**Install the app:** `git` should already be installed from UserData (or `sudo dnf install -y git`). Replace the URL with **your** fork or upstream repo (**HTTPS** is simplest for a public repo):

```bash
# Directory must be empty — git refuses otherwise. If you created `venv` here
# before cloning, or see: "destination path ... already exists and is not an empty directory",
# this rm removes that too (venv is not in git; you will recreate it after clone).
sudo rm -rf /opt/j-answer/app
sudo mkdir -p /opt/j-answer/app
sudo chown ec2-user:ec2-user /opt/j-answer/app

sudo -u ec2-user git clone https://github.com/YOUR_ACCOUNT/j-answer.git /opt/j-answer/app
```

After a successful clone, run the **Python venv** block below again so **`venv`** lives **inside** the cloned tree.

**Private GitHub repo:** use a **personal access token** (HTTPS) or configure **SSH keys** for `ec2-user` and clone `git@github.com:YOUR_ACCOUNT/j-answer.git`. Do not commit tokens into the template; paste the clone URL only in the Session Manager shell.

**Python venv and dependencies** (still as **`ec2-user`**):

```bash
sudo -u ec2-user bash -lc '
set -e
cd /opt/j-answer/app
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
'
```

### 4.2 Frontend build on the server + publishing `web/dist`

nginx serves files from **`/opt/j-answer/web/dist`**, while the Vite project lives in **`/opt/j-answer/app/web/`**. Build on the instance, then copy the build output:

```bash
sudo dnf install -y nodejs npm
node -v
```

This repo targets **Node.js 20+** (see root **`README.md`**). Amazon Linux’s **`nodejs`** package can be older; if **`npm run build`** fails, install a newer Node (e.g. [NodeSource instructions](https://github.com/nodesource/distributions) for EL-based distros) until **`node -v`** is **v20+**, then retry.

```bash
sudo -u ec2-user bash -lc '
set -e
cd /opt/j-answer/app/web
npm ci
npm run build
cp -a dist/. /opt/j-answer/web/dist/
'
```

The SPA calls **`/api/...`** on the same host, so you do **not** need **`VITE_API_BASE`** when nginx proxies as in the example.

#### Routine updates (`git pull`)

After **`main`** (or your branch) is pushed to GitHub, on the server:

```bash
sudo -u ec2-user bash -lc '
set -e
cd /opt/j-answer/app
git pull
cd web && npm ci && npm run build && cd ..
cp -a web/dist/. /opt/j-answer/web/dist/
./venv/bin/pip install -r requirements.txt
'
sudo systemctl restart janswer-api
```

If **`janswer-api`** is not installed yet, skip the **`systemctl restart`** until §4.6.

**Optional — laptop build + `scp`:** If you prefer to build on Windows where **`node -v`** is already 20+, run **`npm run build`** locally, then upload with **`scp`** — **only if SSH key auth works** for `ec2-user` (same requirement as **`ssh`**, see §4.3). Example:  
`scp -r web/dist/* ec2-user@YOUR_HOST:/opt/j-answer/web/dist/`

### 4.3 SQLite database

Place the database on the server (first time or refresh).

**Copy from your PC with `scp` (needs SSH keys):** `scp` uses the same SSH connection as **`ssh`**. If the instance was created **without** an EC2 **key pair** (`KeyName` empty in CloudFormation), **`ec2-user`** has **no** entry in **`~/.ssh/authorized_keys`**, so **`scp`** and **`ssh`** fail with **`Permission denied (publickey, …)`**.

Pick one approach:

1. **Install your laptop’s public key over Session Manager (simplest)**  
   On your PC, show your **public** key (create a key with **`ssh-keygen`** if you do not have one):  
   `Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub`  
   In **Session Manager** (as a user that can **`sudo`**):

   ```bash
   sudo mkdir -p /home/ec2-user/.ssh
   sudo chmod 700 /home/ec2-user/.ssh
   echo 'ssh-ed25519 AAAA...your-public-key... comment' | sudo tee -a /home/ec2-user/.ssh/authorized_keys
   sudo chmod 600 /home/ec2-user/.ssh/authorized_keys
   sudo chown -R ec2-user:ec2-user /home/ec2-user/.ssh
   ```

   Then from **PowerShell** (use the **matching private** key; adjust path):

   ```powershell
   scp -i $env:USERPROFILE\.ssh\id_ed25519 .\j-answer.db ec2-user@54.163.87.182:/opt/j-answer/data/j-answer.db
   ```

2. **[EC2 Instance Connect](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-connect-methods.html#ec2-instance-connect-connecting-aws-cli)**  
   Push an ephemeral public key with the AWS CLI (**`send-ssh-public-key`**), then run **`scp`** within **60 seconds** using the same private key. You need the instance **ID** and **Availability Zone**.

3. **No SSH at all**  
   Upload the file to **S3** from your PC (`aws s3 cp`), then on the instance **`aws s3 cp`** into **`/opt/j-answer/data/`** (the instance role must allow that object; the default j-answer stack does **not** include S3 permissions, so you would extend IAM or use a pre-signed URL).

Example once SSH + key work:

```powershell
scp -i $env:USERPROFILE\.ssh\id_ed25519 .\j-answer.db ec2-user@YOUR_HOST:/opt/j-answer/data/j-answer.db
```

**Updating the `.db` later:** Stop the API (optional but avoids rare locks), overwrite the file, fix ownership if needed, start the API:

```bash
sudo systemctl stop janswer-api
# upload new j-answer.db to /opt/j-answer/data/j-answer.db (scp, SFTP, S3, etc.)
sudo chown ec2-user:ec2-user /opt/j-answer/data/j-answer.db
sudo systemctl start janswer-api
```

### 4.4 nginx

Install Certbot’s nginx plugin, then copy the site config from the clone (paths assume **`/opt/j-answer/app`** is the repo root):

```bash
sudo dnf install -y certbot python3-certbot-nginx
sudo cp /opt/j-answer/app/deploy/nginx-janswer.conf.example /etc/nginx/conf.d/janswer.conf
# Edit if your hostname or paths differ from the example.
sudo nginx -t && sudo systemctl reload nginx
```

The committed example uses **`server_name j-answer.kylemeister.dev`** and **`root /opt/j-answer/web/dist`**. Adjust only if you change hostname or paths.

### 4.5 TLS (Let’s Encrypt)

DNS for `j-answer.kylemeister.dev` must resolve to this instance **before** running Certbot:

```bash
sudo certbot --nginx -d j-answer.kylemeister.dev
```

Follow prompts; Certbot will adjust nginx for HTTPS and renewals.

### 4.6 systemd (FastAPI / Uvicorn)

Copy the systemd unit from the same clone:

```bash
sudo cp /opt/j-answer/app/deploy/janswer-api.service.example /etc/systemd/system/janswer-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now janswer-api
```

Edit the unit if needed:

- **`Environment=JANSWER_DB=`** — path to SQLite (default in example: `/opt/j-answer/data/j-answer.db`).
- **`Environment=CORS_ORIGINS=`** — with **same-origin** nginx + `/api` proxy, the SPA does not need a separate API origin; you can keep the example `https://j-answer.kylemeister.dev` or align after a domain change (see below).

Check:

```bash
curl -sS http://127.0.0.1:8000/api/health
```

#### Troubleshooting: `502 Bad Gateway` from nginx on `/api/...`

nginx returns **502** when the **upstream** (`127.0.0.1:8000`) is **down**, **rejects the connection**, or **closes** before a valid HTTP response. On the instance, narrow it down:

1. **Is Uvicorn running?**

   ```bash
   sudo systemctl status janswer-api --no-pager
   sudo journalctl -u janswer-api -n 80 --no-pager
   ```

   If **inactive** or **failed**, fix errors in the log (wrong **`ExecStart`** path, missing **`venv`**, import errors, etc.), then **`sudo systemctl restart janswer-api`**.

2. **Bypass nginx** — if this fails, the API is the problem, not nginx:

   ```bash
   curl -v http://127.0.0.1:8000/api/health
   curl -v http://127.0.0.1:8000/api/random-clue
   ```

   **`Connection refused`** → nothing is listening on **8000** (service not running or wrong **`--host` / `--port`**).

3. **SELinux (Amazon Linux 2023)** — nginx may be blocked from connecting to the backend:

   ```bash
   getenforce
   sudo tail -n 30 /var/log/nginx/error.log
   ```

   If the log mentions **Permission denied** connecting to upstream, try (persists across reboots):

   ```bash
   sudo setsebool -P httpd_can_network_connect 1
   sudo systemctl restart nginx
   ```

4. **Database readable by `ec2-user`** — the unit runs as **`ec2-user`**. If **`j-answer.db`** is owned by **`root`** with mode **600**, SQLite can fail at runtime:

   ```bash
   ls -la /opt/j-answer/data/j-answer.db
   sudo chown ec2-user:ec2-user /opt/j-answer/data/j-answer.db
   sudo systemctl restart janswer-api
   ```

5. **HTTPS server block after Certbot** — ensure the **`listen 443 ssl`** server has the same **`location /api/`** **`proxy_pass`** as port **80**. Inspect effective config:

   ```bash
   sudo nginx -T 2>/dev/null | grep -A2 "location /api"
   ```

---

## 5. Optional: GitHub Actions deploy on every push to `main`

Workflow: `.github/workflows/deploy-ec2.yml`.

**Repository secrets** (GitHub → **Settings** → **Secrets and variables** → **Actions**):

| Secret | Meaning |
| ------ | ------- |
| `DEPLOY_HOST` | Elastic IP or public DNS of the instance. |
| `DEPLOY_USER` | `ec2-user` on AL2023. |
| `DEPLOY_SSH_KEY` | Private key for a **deploy-only** key in `~/.ssh/authorized_keys` on the server. |

The workflow builds `web/` on **GitHub’s Linux runner**, **rsync**s `janswer/`, `src/`, `requirements.txt`, and `web/dist/` to `/opt/j-answer/`, runs `pip install`, and **`systemctl restart janswer-api`**. It does **not** upload the database; keep managing `j-answer.db` separately (see §4.3). For a **git-only** server workflow, you can later change the Action to **`git pull`** over SSH instead of rsync if you prefer.

GitHub-hosted runners use **dynamic IPs**, so you cannot lock security group port 22 to “GitHub only” without a **self-hosted runner**, **VPN**, or switching to **S3 + SSM**. For a small hobby instance, key-only SSH and **`AllowSSHFromInternet=true`** during active development is the straightforward choice.

---

## 6. Changing the public hostname later (e.g. `j-answer.com`)

When you buy a domain (example: **`j-answer.com`**), you are repeating the same three ideas: **DNS → nginx `server_name` → TLS certificate**, plus **CORS** if the browser ever talks to a different origin than the API.

1. **DNS**  
   At your **registrar** (GoDaddy, Namecheap, etc.) or **Route 53**, create an **`A`** record for the new name (e.g. `j-answer.com` or `app.j-answer.com`) pointing to the **same Elastic IP**, or switch to a load balancer later if you outgrow one instance.

2. **CloudFormation (Route 53 only)**  
   If the domain’s zone is in **Route 53 in this account**, you can update **`DnsRecordName`** / **`HostedZoneId`** in the template deploy. Otherwise manage DNS in the registrar console; the instance only needs the name to resolve to its IP.

3. **nginx**  
   Edit **`server_name`** in `/etc/nginx/conf.d/janswer.conf` to include the new FQDN (you can list multiple names). Run `sudo nginx -t && sudo systemctl reload nginx`.

4. **Certbot**  
   Issue or expand the certificate, e.g.  
   `sudo certbot --nginx -d j-answer.com -d www.j-answer.com`  
   (adjust hosts to match what you serve).

5. **systemd / CORS**  
   If the site and API stay on the **same origin** (recommended), you often only need to update **`CORS_ORIGINS`** to the new canonical `https://...` for consistency. If you split static and API across origins, set **`CORS_ORIGINS`** to the exact browser origin(s) allowed to call the API.

6. **Search engines / bookmarks**  
   Optionally keep the old name as a redirect to the new hostname in nginx until traffic moves over.

---

## 7. Stack layout (reference)

| Piece | Role |
| ----- | ---- |
| **CloudFormation** | EC2, Elastic IP, security group (80/443; optional 22), IAM profile with **SSM**; optional **Route 53** `A` record if you pass **`HostedZoneId`**. |
| **DNS (typical)** | **GoDaddy** (or registrar): **`A`** `j-answer` → Elastic IP; apex **`@`** unchanged for the portfolio. |
| **nginx** | Serves `web/dist`, proxies `/api/` to Uvicorn on `127.0.0.1:8000`. |
| **systemd** | `janswer-api` runs Uvicorn with `JANSWER_DB` set. |
| **SQLite** | File on disk under `/opt/j-answer/data/`; replace by upload when you refresh data. |

Template source: `infra/cloudformation/ec2-janswer.yaml`. Example configs: `deploy/nginx-janswer.conf.example`, `deploy/janswer-api.service.example`.
