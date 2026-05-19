#!/usr/bin/env bash
# j-answer: one-shot, idempotent setup for Amazon Linux 2023 on EC2.
#
# Assumes the CloudFormation template (infra/cloudformation/ec2-janswer.yaml)
# already ran UserData on first boot, which:
#   - installed nginx, python3, git, certbot+nginx plugin, nodejs20
#   - created /opt/j-answer/{app,data,web/dist}
#   - cloned this repo into /opt/j-answer/app
#   - added a 1 GiB swapfile
#
# Run AFTER:
#   1) DNS for --domain resolves to this instance's Elastic IP
#   2) You scp'd j-answer.db to /opt/j-answer/data/j-answer.db
#
# Usage (all flags optional; prompts for what is missing):
#   sudo /opt/j-answer/app/deploy/bootstrap.sh \
#     --domain   j-answer.kylemeister.dev \
#     --email    you@example.com \
#     --openai-key sk-...             # (or set OPENAI_API_KEY in env, or pre-populate /etc/janswer.env)
#     [--skip-tls]                    # don't run certbot (e.g. you haven't pointed DNS yet)
#     [--skip-frontend]               # don't npm ci / build (Python-only redeploy)
#     [--branch main]                 # git ref to check out before building
#
# Re-runnable. Updates the repo, rebuilds, restarts. If /etc/janswer.env already
# has OPENAI_API_KEY the script won't ask again.

set -euo pipefail

APP_USER=ec2-user
APP_DIR=/opt/j-answer/app
DATA_DIR=/opt/j-answer/data
WEB_DIST_DIR=/opt/j-answer/web/dist
DB_PATH="$DATA_DIR/j-answer.db"
ENV_FILE=/etc/janswer.env
SYSTEMD_UNIT=/etc/systemd/system/janswer-api.service
NGINX_CONF=/etc/nginx/conf.d/janswer.conf

DOMAIN=""
EMAIL=""
OPENAI_KEY="${OPENAI_API_KEY:-}"
SKIP_TLS=0
SKIP_FRONTEND=0
BRANCH=""

usage() {
  sed -n '2,28p' "$0"
  exit "${1:-0}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --domain)        DOMAIN="$2"; shift 2;;
    --email)         EMAIL="$2"; shift 2;;
    --openai-key)    OPENAI_KEY="$2"; shift 2;;
    --branch)        BRANCH="$2"; shift 2;;
    --skip-tls)      SKIP_TLS=1; shift;;
    --skip-frontend) SKIP_FRONTEND=1; shift;;
    -h|--help)       usage 0;;
    *) echo "unknown arg: $1" >&2; usage 1;;
  esac
done

step()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
note()  { printf '    %s\n' "$*"; }
warn()  { printf '\033[1;33mwarn:\033[0m %s\n' "$*" >&2; }
fail()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$EUID" -eq 0 ] || fail "Run with sudo (writes /etc/janswer.env, systemd unit, nginx config)."

[ -d "$APP_DIR/.git" ] || fail "$APP_DIR is not a git checkout. UserData should have cloned it — see /var/log/janswer-userdata.log."

# --------- 1. Pull latest code ---------
step "Updating repo at $APP_DIR"
if [ -n "$BRANCH" ]; then
  sudo -u "$APP_USER" git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
  sudo -u "$APP_USER" git -C "$APP_DIR" checkout --quiet "$BRANCH"
fi
sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only --quiet || warn "git pull failed; continuing with current checkout"

# --------- 2. Database sanity ---------
if [ ! -f "$DB_PATH" ]; then
  warn "$DB_PATH not found."
  note "Upload your SQLite database from your laptop, e.g.:"
  note "  scp -i <key.pem> ./j-answer.db ec2-user@<host>:$DB_PATH"
  note "Then re-run this script. The API will keep restarting (and failing health checks) until the DB exists."
fi

# --------- 3. Python venv + deps ---------
step "Setting up Python venv and installing dependencies"
sudo -u "$APP_USER" bash -lc "
  set -euo pipefail
  cd '$APP_DIR'
  if [ ! -d venv ]; then python3 -m venv venv; fi
  ./venv/bin/pip install --quiet --upgrade pip
  ./venv/bin/pip install --quiet -r requirements.txt
"

# --------- 4. Frontend build ---------
if [ "$SKIP_FRONTEND" -eq 0 ]; then
  step "Building frontend (npm ci + vite build)"
  if ! command -v npm >/dev/null 2>&1; then
    fail "npm not found. UserData should have installed nodejs20; check /var/log/janswer-userdata.log."
  fi
  sudo -u "$APP_USER" bash -lc "
    set -euo pipefail
    cd '$APP_DIR/web'
    npm ci --silent
    npm run build --silent
    mkdir -p '$WEB_DIST_DIR'
    cp -a dist/. '$WEB_DIST_DIR/'
  "
else
  note "Skipping frontend build (--skip-frontend)."
fi

# --------- 5. Env file (OpenAI key + per-host overrides) ---------
step "Ensuring $ENV_FILE has OPENAI_API_KEY"
existing_key=""
if [ -f "$ENV_FILE" ]; then
  existing_key="$(grep -E '^OPENAI_API_KEY=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
fi
if [ -z "$OPENAI_KEY" ] && [ -z "$existing_key" ]; then
  if [ -t 0 ]; then
    printf 'Paste OpenAI API key (input hidden): ' >&2
    stty -echo; read -r OPENAI_KEY; stty echo; echo
  else
    fail "OPENAI_API_KEY not provided and no TTY to prompt. Re-run with --openai-key or set OPENAI_API_KEY in your environment."
  fi
fi
EFFECTIVE_KEY="${OPENAI_KEY:-$existing_key}"
[ -n "$EFFECTIVE_KEY" ] || fail "OpenAI API key is empty after prompt."

CORS_LINE=""
if [ -n "$DOMAIN" ]; then
  CORS_LINE="CORS_ORIGINS=https://$DOMAIN"
fi

# Write atomically with secure perms (root:ec2-user 0640 — systemd reads it as ec2-user).
TMP_ENV="$(mktemp)"
{
  echo "# Managed by deploy/bootstrap.sh. Edit values, not the comments."
  echo "OPENAI_API_KEY=$EFFECTIVE_KEY"
  echo "JANSWER_DB=$DB_PATH"
  [ -n "$CORS_LINE" ] && echo "$CORS_LINE"
} > "$TMP_ENV"
install -o root -g "$APP_USER" -m 0640 "$TMP_ENV" "$ENV_FILE"
rm -f "$TMP_ENV"

# --------- 6. systemd unit ---------
step "Installing systemd unit"
install -o root -g root -m 0644 \
  "$APP_DIR/deploy/janswer-api.service.example" "$SYSTEMD_UNIT"
systemctl daemon-reload
systemctl enable --quiet janswer-api

# --------- 7. nginx site config ---------
step "Installing nginx site config"
if [ -n "$DOMAIN" ]; then
  sed "s|j-answer\.kylemeister\.dev|$DOMAIN|g" \
    "$APP_DIR/deploy/nginx-janswer.conf.example" > "$NGINX_CONF"
else
  install -m 0644 "$APP_DIR/deploy/nginx-janswer.conf.example" "$NGINX_CONF"
fi
nginx -t
systemctl reload nginx 2>/dev/null || systemctl restart nginx

# --------- 8. TLS via certbot (optional) ---------
if [ "$SKIP_TLS" -eq 0 ] && [ -n "$DOMAIN" ]; then
  step "Requesting Let's Encrypt certificate for $DOMAIN"
  if [ -z "$EMAIL" ] && [ -t 0 ]; then
    read -r -p "Email for Let's Encrypt expiry notices: " EMAIL
  fi
  if [ -z "$EMAIL" ]; then
    warn "No --email provided; running certbot in --register-unsafely-without-email mode."
    certbot --nginx -d "$DOMAIN" --agree-tos --redirect --non-interactive \
      --register-unsafely-without-email || warn "certbot failed — DNS may not have propagated. Re-run later: sudo certbot --nginx -d $DOMAIN"
  else
    certbot --nginx -d "$DOMAIN" --agree-tos --redirect --non-interactive \
      --email "$EMAIL" || warn "certbot failed — DNS may not have propagated. Re-run later: sudo certbot --nginx -d $DOMAIN"
  fi
elif [ "$SKIP_TLS" -ne 0 ]; then
  note "Skipping TLS (--skip-tls). Site will serve plain HTTP only."
elif [ -z "$DOMAIN" ]; then
  note "No --domain; skipping TLS. Provide --domain later and re-run to enable HTTPS."
fi

# --------- 9. Restart API and health-check ---------
step "Restarting janswer-api"
systemctl restart janswer-api

# Give uvicorn a beat to come up + pre-warm to start.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS -m 2 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

step "Health check"
if curl -fsS -m 5 http://127.0.0.1:8000/api/health | grep -q '"ok"'; then
  echo "    /api/health: ok"
else
  warn "/api/health failed — inspect: sudo journalctl -u janswer-api -n 100 --no-pager"
  exit 1
fi

if [ -f "$DB_PATH" ]; then
  if curl -fsS -m 10 http://127.0.0.1:8000/api/embeddings/status >/dev/null 2>&1; then
    echo "    /api/embeddings/status: ok (Magic index pre-warming in background)"
  else
    warn "/api/embeddings/status failed — DB may be unreadable by $APP_USER. Check: ls -la $DB_PATH"
  fi
fi

step "Done"
note "Logs:        sudo journalctl -u janswer-api -f"
note "Env file:    $ENV_FILE  (chmod 0640 root:$APP_USER)"
note "Site:        ${DOMAIN:+https://}${DOMAIN:-http://<elastic-ip>}"
note "Re-run me any time to ship new code + rebuild + restart."
