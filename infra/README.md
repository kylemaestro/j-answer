# Infrastructure (AWS)

End-to-end deployment steps (CloudFormation, **GoDaddy DNS**, nginx, TLS, systemd, database uploads, optional GitHub Actions) live in **`docs/aws.md`**.

This folder holds the **CloudFormation** template: `cloudformation/ec2-janswer.yaml`.

Quick deploy from the repository root (resolve **default VPC** in the same region, then pass **`VpcId`**; add the **`j-answer`** `A` record in GoDaddy using the stack’s **PublicIp** output — see **`docs/aws.md`** §2–§3):

```bash
DEFAULT_VPC=$(aws ec2 describe-vpcs --region us-east-1 --filters Name=isDefault,Values=true --query "Vpcs[0].VpcId" --output text)
aws cloudformation deploy \
  --region us-east-1 \
  --stack-name j-answer-app \
  --template-file infra/cloudformation/ec2-janswer.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides VpcId="${DEFAULT_VPC}" InstanceType=t4g.micro AllowSSHFromInternet=true
```

Optional: if `kylemeister.dev` is a **Route 53 hosted zone in this AWS account**, add `HostedZoneId=Z...` to **`--parameter-overrides`** so the stack creates the DNS record.

See **`docs/aws.md`** for GoDaddy steps, x86 AMI overrides, and server bootstrap.
