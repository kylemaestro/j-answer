# Infrastructure (AWS)

## CloudFormation — single EC2 + Elastic IP

Template: `cloudformation/ec2-janswer.yaml`

- Default instance: **Amazon Linux 2023** on **ARM64** (`t4g.micro`) with the SSM default AMI parameter.
- If you use **x86** (e.g. `t3.micro`), override **`LatestAmiId`** to  
  `/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64`.
- **IAM**: instance profile includes **AmazonSSMManagedInstanceCore** (Session Manager, no inbound SSH required if you prefer).
- **Optional SSH** for `rsync` from GitHub Actions: parameter **`AllowSSHFromInternet`** (default `true` opens port 22 to the world — tighten or set `false` after you move to SSM/S3 deploy).

### Deploy the stack

From the **repository root**:

```bash
aws cloudformation deploy \
  --region us-east-1 \
  --stack-name j-answer-app \
  --template-file infra/cloudformation/ec2-janswer.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides InstanceType=t4g.micro AllowSSHFromInternet=true
```

Optional key pair for emergency SSH:

```bash
  --parameter-overrides InstanceType=t4g.micro KeyName=my-key AllowSSHFromInternet=true
```

Read stack outputs (Elastic IP for Route53):

```bash
aws cloudformation describe-stacks --stack-name j-answer-app --query "Stacks[0].Outputs" --output table
```

Full server setup (nginx, TLS, systemd, first app deploy) is in the main repository **README** under **Deployment and Infrastructure**.
