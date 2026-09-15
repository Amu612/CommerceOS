# Deploying CommerceOS on the AWS Free Tier

A single-EC2 deployment: one `t3.micro` instance runs `backend` + `worker` +
`frontend` via `docker-compose.aws.yml`, talking to a managed **RDS
PostgreSQL** (`db.t3.micro`) and **ElastiCache Redis** (`cache.t3.micro`) —
all three Free Tier eligible (750 instance-hours/month each, enough for
exactly one of each running 24/7, for the first 12 months of a new AWS
account). No ALB, no NAT Gateway, no ECS/Fargate — those have no Free Tier
coverage at all and are a different, more expensive path (see
`infra/aws/README.md`).

**Before anything else**, confirm your Free Tier window is actually active:
console.aws.amazon.com → Billing → **Free Tier**. If it's expired (accounts
older than 12 months), this same setup still works, just isn't free — budget
roughly $30-35/month against your credit instead of ~$0.

This doc is the condensed reference. An interactive, checklist version with
copy-able commands was generated alongside it — ask for it again any time by
name ("the AWS free tier deploy guide").

## 0. Guardrails first

1. **Set a budget alert** before creating anything:
   ```bash
   aws budgets create-budget --account-id $(aws sts get-caller-identity --query Account --output text) \
     --budget '{"BudgetName":"commerceos-guard","BudgetLimit":{"Amount":"20","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}' \
     --notifications-with-subscribers '[{"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":50},"Subscribers":[{"SubscriptionType":"EMAIL","Address":"you@example.com"}]}]'
   ```
   (Or Console → Billing → Budgets → Create budget — same effect, no CLI needed.)
2. Create an IAM user for CLI/deploy use — don't use root account keys. Console
   → IAM → Users → Create user → attach `AdministratorAccess` (fine for a
   personal learning account; scope it down for anything else) → create an
   access key → `aws configure`.
3. Pick a region: `us-east-1` has the broadest Free Tier service availability.

## 1. Networking — reuse the account's default VPC

```bash
export AWS_REGION=us-east-1
VPC_ID=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
SUBNET_IDS=$(aws ec2 describe-subnets --filters Name=vpc-id,Values=$VPC_ID --query 'Subnets[].SubnetId' --output text)
SUBNET_ARR=($SUBNET_IDS)
MYIP="$(curl -s https://checkip.amazonaws.com)/32"

EC2_SG=$(aws ec2 create-security-group --group-name commerceos-ec2-sg --description "CommerceOS app" --vpc-id $VPC_ID --query GroupId --output text)
aws ec2 authorize-security-group-ingress --group-id $EC2_SG --protocol tcp --port 22 --cidr $MYIP
aws ec2 authorize-security-group-ingress --group-id $EC2_SG --protocol tcp --port 80 --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress --group-id $EC2_SG --protocol tcp --port 8000 --cidr 0.0.0.0/0

RDS_SG=$(aws ec2 create-security-group --group-name commerceos-rds-sg --description "CommerceOS RDS" --vpc-id $VPC_ID --query GroupId --output text)
aws ec2 authorize-security-group-ingress --group-id $RDS_SG --protocol tcp --port 5432 --source-group $EC2_SG

REDIS_SG=$(aws ec2 create-security-group --group-name commerceos-redis-sg --description "CommerceOS Redis" --vpc-id $VPC_ID --query GroupId --output text)
aws ec2 authorize-security-group-ingress --group-id $REDIS_SG --protocol tcp --port 6379 --source-group $EC2_SG
```

## 2. RDS PostgreSQL

```bash
aws rds create-db-subnet-group --db-subnet-group-name commerceos-subnets \
  --db-subnet-group-description "CommerceOS" --subnet-ids $SUBNET_IDS

DB_PASSWORD=$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | head -c 24)
echo "DB password (save this): $DB_PASSWORD"

aws rds create-db-instance \
  --db-instance-identifier commerceos-db \
  --db-instance-class db.t3.micro \
  --engine postgres \
  --master-username commerceos_app \
  --master-user-password "$DB_PASSWORD" \
  --allocated-storage 20 --storage-type gp2 \
  --db-name commerceos \
  --vpc-security-group-ids $RDS_SG \
  --db-subnet-group-name commerceos-subnets \
  --no-multi-az --no-publicly-accessible \
  --backup-retention-period 1

aws rds wait db-instance-available --db-instance-identifier commerceos-db
RDS_ENDPOINT=$(aws rds describe-db-instances --db-instance-identifier commerceos-db \
  --query 'DBInstances[0].Endpoint.Address' --output text)
```

## 3. ElastiCache Redis

```bash
aws elasticache create-cache-subnet-group --cache-subnet-group-name commerceos-redis-subnets \
  --cache-subnet-group-description "CommerceOS" --subnet-ids $SUBNET_IDS

aws elasticache create-cache-cluster \
  --cache-cluster-id commerceos-redis \
  --engine redis \
  --cache-node-type cache.t3.micro \
  --num-cache-nodes 1 \
  --cache-subnet-group-name commerceos-redis-subnets \
  --security-group-ids $REDIS_SG

aws elasticache wait cache-cluster-available --cache-cluster-id commerceos-redis
REDIS_ENDPOINT=$(aws elasticache describe-cache-clusters --cache-cluster-id commerceos-redis \
  --show-cache-node-info --query 'CacheClusters[0].CacheNodes[0].Endpoint.Address' --output text)
```

## 4. EC2 instance

`cloud-init.sh` (save locally first):
```bash
#!/bin/bash
set -e
dnf update -y
dnf install -y docker git
systemctl enable --now docker
usermod -aG docker ec2-user
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
mkdir -p /usr/local/lib/docker/cli-plugins
curl -SL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
```

```bash
AMI_ID=$(aws ssm get-parameters --names /aws/service/ami-amazon-linux-2023/x86_64/latest/image_id \
  --query 'Parameters[0].Value' --output text)

aws ec2 create-key-pair --key-name commerceos-key --query 'KeyMaterial' --output text > commerceos-key.pem
chmod 400 commerceos-key.pem

INSTANCE_ID=$(aws ec2 run-instances \
  --image-id $AMI_ID --instance-type t3.micro --key-name commerceos-key \
  --security-group-ids $EC2_SG --subnet-id ${SUBNET_ARR[0]} \
  --block-device-mappings '[{"DeviceName":"/dev/xvda","Ebs":{"VolumeSize":25,"VolumeType":"gp3"}}]' \
  --user-data file://cloud-init.sh \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=commerceos}]' \
  --query 'Instances[0].InstanceId' --output text)

aws ec2 wait instance-running --instance-ids $INSTANCE_ID

EIP_ALLOC=$(aws ec2 allocate-address --domain vpc --query AllocationId --output text)
aws ec2 associate-address --instance-id $INSTANCE_ID --allocation-id $EIP_ALLOC
EIP=$(aws ec2 describe-addresses --allocation-ids $EIP_ALLOC --query 'Addresses[0].PublicIp' --output text)
echo "Elastic IP: $EIP"
```

## 5. Deploy the app

```bash
ssh -i commerceos-key.pem ec2-user@$EIP
```
On the instance:
```bash
git clone https://github.com/Amu612/CommerceOS.git
cd CommerceOS
cp .env.aws.example .env.aws
# edit .env.aws: DATABASE_URL, REDIS_URL (from the endpoints above), JWT_SECRET
# (openssl rand -hex 32), PUBLIC_URL=http://<Elastic IP>, PUBLIC_WS_URL=ws://<Elastic IP>
docker compose -f docker-compose.aws.yml --env-file .env.aws up -d --build
docker compose -f docker-compose.aws.yml logs -f migrate   # watch it seed, then Ctrl-C
```

## 6. Verify

```bash
curl http://$EIP:8000/health
curl http://$EIP/
```
Then open `http://<Elastic IP>/` in a browser and log in with `admin` /
`CommerceOS2026!` (or whatever `SEED_ADMIN_PASSWORD` you set).

## 7. CI/CD

`.github/workflows/deploy-ec2.yml` SSHes in and redeploys on every push to
`main`. Set repo secrets `EC2_HOST` (the Elastic IP) and `EC2_SSH_KEY` (the
contents of `commerceos-key.pem`, or a dedicated deploy-only key pair —
recommended so CI doesn't hold the same key you use interactively).

## 8. Cost hygiene

- Free Tier gives you 750 hours/month each of EC2 t3.micro, RDS t3.micro,
  and ElastiCache t3.micro — enough for exactly **one** of each, running
  continuously. Don't leave a second instance of any of them running
  alongside it.
- RDS can be paused: `aws rds stop-db-instance --db-instance-identifier commerceos-db`
  (auto-resumes after 7 days — restart it manually before then if you want
  it to stay stopped). ElastiCache has no stop/start; delete and recreate if
  you need a real pause.
- Check console.aws.amazon.com/billing/home#/freetier weekly while learning
  this — it shows Free Tier usage against the 750h caps in near-real-time.

## 9. Teardown (delete everything, in dependency order)

```bash
aws ec2 terminate-instances --instance-ids $INSTANCE_ID
aws ec2 wait instance-terminated --instance-ids $INSTANCE_ID
aws ec2 release-address --allocation-id $EIP_ALLOC

aws rds delete-db-instance --db-instance-identifier commerceos-db --skip-final-snapshot
aws rds wait db-instance-deleted --db-instance-identifier commerceos-db

aws elasticache delete-cache-cluster --cache-cluster-id commerceos-redis
aws elasticache wait cache-cluster-deleted --cache-cluster-id commerceos-redis

aws rds delete-db-subnet-group --db-subnet-group-name commerceos-subnets
aws elasticache delete-cache-subnet-group --cache-subnet-group-name commerceos-redis-subnets

aws ec2 delete-security-group --group-id $RDS_SG
aws ec2 delete-security-group --group-id $REDIS_SG
aws ec2 delete-security-group --group-id $EC2_SG

aws ec2 delete-key-pair --key-name commerceos-key
rm -f commerceos-key.pem
```

## Relationship to `infra/aws/`

`infra/aws/` (Terraform + `deploy.yml`/`infra.yml`) is a *separate*,
more production-shaped path — ECS Fargate, an ALB, autoscaling, Multi-AZ RDS
in prod — described in `infra/aws/README.md`. It costs real money from hour
one (~$140-150/month even at dev sizing) because Fargate/ALB/NAT have no
Free Tier coverage. Use this EC2 guide to stay free; graduate to `infra/aws/`
later if/when you need the scalability and are ready to pay for it.
