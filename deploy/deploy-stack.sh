#!/usr/bin/env bash
# Deploy (or update) the j-answer CloudFormation stack from your laptop.
#
# Setup:
#   cp deploy/deploy-stack.env.example deploy/deploy-stack.env
#   # edit deploy-stack.env
#
# Run from the repository root:
#   bash deploy/deploy-stack.sh
#
# Requires: AWS CLI v2, aws sts get-caller-identity works.
# On Windows: use Git Bash or WSL — not PowerShell (use deploy-stack.ps1 instead).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

ENV_FILE="$REPO_ROOT/deploy/deploy-stack.env"
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  set -a
  source "$ENV_FILE"
  set +a
else
  echo "hint: copy deploy/deploy-stack.env.example to deploy/deploy-stack.env and edit it." >&2
fi

AWS_REGION="${AWS_REGION:-us-east-1}"
STACK_NAME="${STACK_NAME:-j-answer-app}"
DOMAIN="${DOMAIN:-j-answer.kylemeister.dev}"
DB_PATH="${DB_PATH:-./j-answer.db}"
SSH_KEY_PATH="${SSH_KEY_PATH:-$HOME/.ssh/id_ed25519}"
INSTANCE_TYPE="${INSTANCE_TYPE:-t4g.small}"
ROOT_VOLUME_GIB="${ROOT_VOLUME_GIB:-16}"
REPO_URL="${REPO_URL:-https://github.com/kylemaestro/j-answer.git}"
REPO_REF="${REPO_REF:-master}"
ALLOW_SSH_FROM_INTERNET="${ALLOW_SSH_FROM_INTERNET:-true}"
UPLOAD_DB="${UPLOAD_DB:-false}"

TEMPLATE="$REPO_ROOT/infra/cloudformation/ec2-janswer.yaml"

step() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

command -v aws >/dev/null 2>&1 || fail "aws CLI not found. Install AWS CLI v2 and configure credentials."

step "Checking AWS credentials"
aws sts get-caller-identity --region "$AWS_REGION" >/dev/null

if [ -z "${VPC_ID:-}" ] || [ "$VPC_ID" = "None" ]; then
  step "Resolving default VPC in $AWS_REGION"
  VPC_ID="$(aws ec2 describe-vpcs --region "$AWS_REGION" \
    --filters Name=isDefault,Values=true \
    --query "Vpcs[0].VpcId" --output text)"
fi
[ -n "$VPC_ID" ] && [ "$VPC_ID" != "None" ] || fail "No default VPC in $AWS_REGION. Set VPC_ID in deploy-stack.env."

PARAMS=(
  "VpcId=$VPC_ID"
  "InstanceType=$INSTANCE_TYPE"
  "RootVolumeSizeGiB=$ROOT_VOLUME_GIB"
  "RepoUrl=$REPO_URL"
  "RepoRef=$REPO_REF"
  "AllowSSHFromInternet=$ALLOW_SSH_FROM_INTERNET"
)
[ -n "${SUBNET_ID:-}" ] && PARAMS+=("SubnetId=$SUBNET_ID")
[ -n "${KEY_NAME:-}" ] && PARAMS+=("KeyName=$KEY_NAME")
[ -n "${HOSTED_ZONE_ID:-}" ] && PARAMS+=("HostedZoneId=$HOSTED_ZONE_ID" "DnsRecordName=$DOMAIN")

step "Deploying CloudFormation stack $STACK_NAME in $AWS_REGION"
aws cloudformation deploy \
  --region "$AWS_REGION" \
  --stack-name "$STACK_NAME" \
  --template-file "$TEMPLATE" \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides "${PARAMS[@]}"

step "Stack outputs"
aws cloudformation describe-stacks \
  --region "$AWS_REGION" \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs" \
  --output table

PUBLIC_IP="$(aws cloudformation describe-stacks \
  --region "$AWS_REGION" \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='PublicIp'].OutputValue | [0]" \
  --output text)"

[ -n "$PUBLIC_IP" ] && [ "$PUBLIC_IP" != "None" ] || fail "Could not read PublicIp from stack outputs."

if [ "$UPLOAD_DB" = "true" ] || [ "$UPLOAD_DB" = "1" ]; then
  step "Uploading database to ec2-user@$PUBLIC_IP"
  [ -f "$DB_PATH" ] || fail "DB not found at $DB_PATH (set DB_PATH in deploy-stack.env)"
  EXPANDED_KEY="${SSH_KEY_PATH/#\~/$HOME}"
  scp -i "$EXPANDED_KEY" "$DB_PATH" "ec2-user@${PUBLIC_IP}:/opt/j-answer/data/j-answer.db"
fi

step "Next steps (manual)"
cat <<EOF

  1) DNS: point $DOMAIN → $PUBLIC_IP (GoDaddy A record, or Route 53 if you set HOSTED_ZONE_ID).
     Wait until: nslookup $DOMAIN

  2) Upload DB (if you did not set UPLOAD_DB=true):
     scp -i <key> $DB_PATH ec2-user@${PUBLIC_IP}:/opt/j-answer/data/j-answer.db

  3) Bootstrap on the instance (Session Manager or SSH):
     sudo /opt/j-answer/app/deploy/bootstrap.sh --domain $DOMAIN --email you@example.com

  UserData log (if clone failed): sudo tail -n 50 /var/log/janswer-userdata.log

EOF
