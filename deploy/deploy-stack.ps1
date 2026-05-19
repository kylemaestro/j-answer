# Deploy (or update) the j-answer CloudFormation stack from Windows PowerShell.
#
# Setup:
#   Copy-Item deploy\deploy-stack.env.example deploy\deploy-stack.env
#   notepad deploy\deploy-stack.env
#
# Run from the repository root:
#   .\deploy\deploy-stack.ps1
#
# Requires: AWS CLI v2 (aws.exe on PATH), credentials configured.

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$EnvFile = Join-Path $RepoRoot "deploy\deploy-stack.env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) { return }
        if ($line -match "^([^=]+)=(.*)$") {
            $name = $matches[1].Trim()
            $value = $matches[2].Trim()
            Set-Item -Path "Env:$name" -Value $value
        }
    }
} else {
    Write-Host "hint: copy deploy\deploy-stack.env.example to deploy\deploy-stack.env and edit it." -ForegroundColor Yellow
}

$AwsRegion = if ($env:AWS_REGION) { $env:AWS_REGION } else { "us-east-1" }
$StackName = if ($env:STACK_NAME) { $env:STACK_NAME } else { "j-answer-app" }
$Domain = if ($env:DOMAIN) { $env:DOMAIN } else { "j-answer.kylemeister.dev" }
$DbPath = if ($env:DB_PATH) { $env:DB_PATH } else { ".\j-answer.db" }
$SshKeyPath = if ($env:SSH_KEY_PATH) { $env:SSH_KEY_PATH } else { "$env:USERPROFILE\.ssh\id_ed25519" }
$InstanceType = if ($env:INSTANCE_TYPE) { $env:INSTANCE_TYPE } else { "t4g.small" }
$RootVolumeGib = if ($env:ROOT_VOLUME_GIB) { $env:ROOT_VOLUME_GIB } else { "16" }
$RepoUrl = if ($env:REPO_URL) { $env:REPO_URL } else { "https://github.com/kylemaestro/j-answer.git" }
$RepoRef = if ($env:REPO_REF) { $env:REPO_REF } else { "main" }
$AllowSsh = if ($env:ALLOW_SSH_FROM_INTERNET) { $env:ALLOW_SSH_FROM_INTERNET } else { "true" }
$UploadDb = ($env:UPLOAD_DB -eq "true") -or ($env:UPLOAD_DB -eq "1")

$Template = Join-Path $RepoRoot "infra\cloudformation\ec2-janswer.yaml"

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host "error: $msg" -ForegroundColor Red; exit 1 }

if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
    Fail "aws CLI not found. Install AWS CLI v2 and configure credentials."
}

Step "Checking AWS credentials"
aws sts get-caller-identity --region $AwsRegion | Out-Null

$VpcId = $env:VPC_ID
if ([string]::IsNullOrWhiteSpace($VpcId) -or $VpcId -eq "None") {
    Step "Resolving default VPC in $AwsRegion"
    $VpcId = aws ec2 describe-vpcs --region $AwsRegion `
        --filters Name=isDefault,Values=true `
        --query "Vpcs[0].VpcId" --output text
}
if ([string]::IsNullOrWhiteSpace($VpcId) -or $VpcId -eq "None") {
    Fail "No default VPC in $AwsRegion. Set VPC_ID in deploy\deploy-stack.env."
}

$ParamOverrides = @(
    "VpcId=$VpcId",
    "InstanceType=$InstanceType",
    "RootVolumeSizeGiB=$RootVolumeGib",
    "RepoUrl=$RepoUrl",
    "RepoRef=$RepoRef",
    "AllowSSHFromInternet=$AllowSsh"
)
if ($env:SUBNET_ID) { $ParamOverrides += "SubnetId=$($env:SUBNET_ID)" }
if ($env:KEY_NAME) { $ParamOverrides += "KeyName=$($env:KEY_NAME)" }
if ($env:HOSTED_ZONE_ID) {
    $ParamOverrides += "HostedZoneId=$($env:HOSTED_ZONE_ID)"
    $ParamOverrides += "DnsRecordName=$Domain"
}

Step "Deploying CloudFormation stack $StackName in $AwsRegion"
aws cloudformation deploy `
    --region $AwsRegion `
    --stack-name $StackName `
    --template-file $Template `
    --capabilities CAPABILITY_IAM `
    --parameter-overrides $ParamOverrides

Step "Stack outputs"
aws cloudformation describe-stacks `
    --region $AwsRegion `
    --stack-name $StackName `
    --query "Stacks[0].Outputs" `
    --output table

$PublicIp = aws cloudformation describe-stacks `
    --region $AwsRegion `
    --stack-name $StackName `
    --query "Stacks[0].Outputs[?OutputKey=='PublicIp'].OutputValue | [0]" `
    --output text

if ([string]::IsNullOrWhiteSpace($PublicIp) -or $PublicIp -eq "None") {
    Fail "Could not read PublicIp from stack outputs."
}

if ($UploadDb) {
    Step "Uploading database to ec2-user@${PublicIp}"
    $DbFull = Resolve-Path $DbPath -ErrorAction Stop
    scp -i $SshKeyPath $DbFull "ec2-user@${PublicIp}:/opt/j-answer/data/j-answer.db"
}

Step "Next steps (manual)"
Write-Host @"

  1) DNS: point $Domain -> $PublicIp (GoDaddy A record, or Route 53 if you set HOSTED_ZONE_ID).
     Wait until: nslookup $Domain

  2) Upload DB (if you did not set UPLOAD_DB=true):
     scp -i $SshKeyPath $DbPath ec2-user@${PublicIp}:/opt/j-answer/data/j-answer.db

  3) Bootstrap on the instance (Session Manager or SSH):
     sudo /opt/j-answer/app/deploy/bootstrap.sh --domain $Domain --email you@example.com

  UserData log (if clone failed): sudo tail -n 50 /var/log/janswer-userdata.log

"@
