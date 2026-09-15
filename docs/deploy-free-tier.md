# Deploying CommerceOS on AWS — Free Tier, step by step

This deploys the whole app — frontend, backend, worker, PostgreSQL, and
(optionally) Redis — using only AWS Free Tier eligible resources, for
**as close to $0/month as AWS currently allows**. The one unavoidable line
item: since February 2024 AWS charges **~$0.005/hour (~$3.60/month) for any
public IPv4 address**, including the one your EC2 instance needs to be
reachable at all — that's not part of any free tier and there's no way
around it for a publicly-reachable box. Everything else in this guide is
genuinely free-tier eligible. It is a *different, simpler* architecture than
[`infra/aws/`](../infra/aws/README.md): one EC2 instance running
`docker-compose.free-tier.yml`, talking to a free-tier RDS instance and
(optionally) a free-tier ElastiCache node. No ECS, no Fargate, no NAT
Gateway, no Application Load Balancer, no Secrets Manager — none of those
have a meaningful free tier, and the `infra/aws/` Terraform that uses them
runs roughly $140+/month (see `docs/cost-estimate.md`). Use *this* guide
instead if you're on a free-tier account and want to stay at $0.

> **Always double-check "Free Tier eligible" in the AWS Console yourself**
> before creating anything — this guide targets the classic 12-month Free
> Tier (`db.t3.micro`/`t2.micro`/`t3.micro`, 750 hrs/month per service).
> AWS's free-tier program has changed over time and can differ by account
> age/region; the Console always shows the current, authoritative eligible
> options with a "Free tier eligible" badge.

## What you'll end up with

```
GitHub push ──▶ GitHub Actions (SSH) ──▶ EC2 (t2.micro/t3.micro)
                                            ├─ frontend  (nginx, :80)
                                            ├─ backend   (FastAPI, :8000)
                                            └─ worker    (replay engine)
                                                   │
                                     ┌─────────────┴─────────────┐
                                     ▼                            ▼
                          RDS PostgreSQL (db.t3.micro)   ElastiCache Redis (optional)
```

## 0. Before you start

- **Turn on free-tier usage alerts**: Billing Console → *Billing Preferences*
  → check "Receive Free Tier Usage Alerts", enter your email.
- **Set a budget as a safety net**: Billing Console → *Budgets* → *Create
  budget* → "Zero spend budget" (alerts you the moment anything is charged).
  Two minutes, costs nothing, and is the single best protection against a
  surprise bill from a misclick.
- You'll need: an AWS account, the AWS CLI installed and configured
  (`aws configure`) with an account that has admin rights (only for this
  setup — the running app doesn't need any IAM keys), and your GitHub repo
  already pushed (it is).

Pick one region for everything below — `us-east-1` is used in the examples.

## 1. Security groups

Console → **EC2 → Security Groups → Create security group**, three times
(all in your default VPC):

| Name | Inbound rules |
| :--- | :--- |
| `commerceos-ec2-sg` | SSH (22) from **My IP** only · HTTP (80) from `0.0.0.0/0` · Custom TCP (8000) from `0.0.0.0/0` |
| `commerceos-rds-sg` | PostgreSQL (5432) from source = `commerceos-ec2-sg` (select the security group, not a CIDR) |
| `commerceos-redis-sg` | Custom TCP (6379) from source = `commerceos-ec2-sg` |

Restricting RDS/Redis inbound to the EC2 security group (not a CIDR) means
they're unreachable from the internet even though they're "publicly
accessible" at the network level — keep that setting **off** for both
anyway (belt and suspenders, and it's the free-tier-eligible default).

## 2. S3 bucket for the datasets

The Olist + DataCo CSVs are gitignored (~194 MB) so they aren't in your repo
or in any Docker image — upload them once:

```bash
aws s3 mb s3://commerceos-datasets-<your-account-id> --region us-east-1
aws s3api put-public-access-block --bucket commerceos-datasets-<your-account-id> \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3 sync backend/data/raw/ s3://commerceos-datasets-<your-account-id>/raw/
```

5 GB of S3 storage is always free-tier eligible — this dataset is nowhere
close to that limit.

## 3. RDS PostgreSQL (free tier)

Console → **RDS → Create database**:
- Engine: **PostgreSQL** (15.x)
- Templates: **Free tier** (this auto-selects a free-tier-eligible config —
  do not change instance class/storage away from what it picks)
- DB instance identifier: `commerceos-db`
- Master username: `commerceos_app`, master password: generate and save one
- Instance: `db.t3.micro` (or whatever the Free tier template selects)
- Storage: 20 GB gp2/gp3 (the free-tier default — don't increase it)
- Connectivity: your default VPC, **Public access: No**, VPC security group:
  `commerceos-rds-sg`
- Additional configuration: initial database name `commerceos`
- Leave Multi-AZ **off** (Multi-AZ is not free-tier eligible)

Takes a few minutes to become "Available". Copy its **Endpoint** (Console →
RDS → Databases → commerceos-db) — you'll need `<endpoint>:5432`.

## 4. ElastiCache Redis (free tier) — optional

The app runs fine without Redis (`REDIS_ENABLED=false` — see
`backend/app/core/redis.py`); it only loses the pub/sub event bus and some
replay-state persistence across restarts. Skip this section for your first
deploy if you want fewer moving parts, and come back to it later.

Console → **ElastiCache → Redis clusters → Create Redis cluster**:
- Cluster mode: **Disabled**
- Name: `commerceos-redis`
- Node type: `cache.t3.micro` (free tier)
- Number of replicas: 0
- VPC: default, security group: `commerceos-redis-sg`
- Encryption: leave off (keeps `redis://` simple — this traffic never
  leaves your VPC)

Copy its **Primary endpoint** once available.

## 5. EC2 instance (free tier)

Console → **EC2 → Launch instance**:
- Name: `commerceos-app`
- AMI: **Amazon Linux 2023** (Free tier eligible)
- Instance type: `t2.micro` or `t3.micro` — whichever the Console tags
  **"Free tier eligible"** for your account/region
- Key pair: create a new one, download the `.pem`, keep it safe (you need it
  for SSH and for the GitHub Actions secret)
- Network: default VPC, a **public** subnet, auto-assign public IP: enable
- Security group: `commerceos-ec2-sg`
- Storage: 30 GB gp3 is within the free tier (up to 30 GB EBS is included)
- **Advanced details → User data**: paste the contents of
  [`infra/aws/free-tier/ec2-user-data.sh`](../infra/aws/free-tier/ec2-user-data.sh)
  (edit the `REPO_URL` line first if you're deploying a fork; if your repo is
  **private**, the script's comment shows how to embed a personal access
  token in the clone URL, or just skip the auto-clone and `git clone` by hand
  once you SSH in)

Launch it. Then, Console → **EC2 → Elastic IPs → Allocate Elastic IP
address** → Actions → **Associate** it to `commerceos-app`. This gives you a
stable public IP that survives a reboot.

**On the ~$3.60/month IPv4 charge**: since Feb 2024, AWS bills every public
IPv4 address $0.005/hour — attached and in use or not, Elastic IP or the
instance's own auto-assigned public IP. There's no free allowance for it on
most accounts. This is the one real (small) cost in this whole guide; if you
stop the instance without releasing the Elastic IP, you keep paying this
same rate for an address attached to nothing, so release it if you're not
going to restart the instance soon (see "Tearing it down" below).

## 6. First-time server setup

SSH in (give the user-data script ~1-2 minutes to finish installing Docker
first):

```bash
ssh -i your-key.pem ec2-user@<elastic-ip>
docker --version && docker compose version   # confirm the user-data script finished
```

Create the real env file from the template (never commit this):

```bash
cd ~/CommerceOS
cp .env.free-tier.example .env.free-tier
nano .env.free-tier   # fill in DATABASE_URL, JWT_SECRET, SEED_ADMIN_PASSWORD, PUBLIC_* URLs, DATASET_DIR
```

Generate a real JWT secret while you're at it: `openssl rand -hex 32`.
`PUBLIC_URL`/`PUBLIC_API_URL`/`PUBLIC_WS_URL` should use your Elastic IP,
e.g. `http://203.0.113.10`, `http://203.0.113.10:8000`, `ws://203.0.113.10:8000`.

Run the first deploy by hand, so you can watch for errors directly:

```bash
docker compose -f docker-compose.free-tier.yml --env-file .env.free-tier build
docker compose -f docker-compose.free-tier.yml --env-file .env.free-tier run --rm migrate
docker compose -f docker-compose.free-tier.yml --env-file .env.free-tier up -d
docker compose -f docker-compose.free-tier.yml logs -f backend   # ctrl-C once you see it's healthy
```

Visit `http://<elastic-ip>` — you should see the CommerceOS login/dashboard.
Log in with `admin` / whatever you set `SEED_ADMIN_PASSWORD` to.

## 7. Wire up GitHub Actions for ongoing deploys

`.github/workflows/deploy-free-tier.yml` is already in the repo — it SSHes
in on every push to `main` and re-deploys. Add these repo secrets (GitHub
repo → Settings → Secrets and variables → Actions → New repository secret):

| Secret | Value |
| :--- | :--- |
| `EC2_HOST` | your Elastic IP |
| `EC2_USER` | `ec2-user` |
| `EC2_SSH_KEY` | the full contents of the `.pem` file you downloaded |

That's it — no AWS credentials needed in GitHub at all for this path (the
workflow only ever SSHes into your box; the box already has everything it
needs in `.env.free-tier`, which `git pull` never touches since it's
gitignored).

Push to `main` and watch the **Actions** tab — it should pull, rebuild, run
the migration, and restart the containers.

## 8. Everyday operations

- **Logs**: `ssh` in, `docker compose -f docker-compose.free-tier.yml logs -f <backend|worker|frontend>`
- **Restart**: `docker compose -f docker-compose.free-tier.yml restart`
- **Re-seed / rebuild the warehouse**: `docker compose -f docker-compose.free-tier.yml run --rm migrate`
- **Rotate the JWT secret**: edit `.env.free-tier`, then `docker compose -f docker-compose.free-tier.yml up -d` (recreates `backend`/`worker` with the new value — this invalidates existing sessions)

## 9. Staying inside the free tier

- One RDS instance, single-AZ, `db.t3.micro`, ≤20 GB — don't add Multi-AZ or
  a read replica (each is a second billed instance).
- One ElastiCache node, no replicas.
- One EC2 instance. A second one (even `t2.micro`) is a second set of
  free-tier hours — free tier hour *allowances* are typically pooled per
  account per instance family per region, not per-instance, so running two
  micro instances simultaneously can exceed the monthly hour cap.
- No NAT Gateway, no Elastic Load Balancer, no CloudFront/WAF, no Secrets
  Manager — this guide's architecture uses none of them, which is exactly
  what keeps it free. (`infra/aws/` uses all of them — that's a different,
  paid path; see its README.)
- Watch data transfer **out** — 100 GB/month outbound is free account-wide;
  this app's traffic won't come close unless you're load-testing it.
- Check **Billing → Free Tier** in the Console periodically — it shows
  exactly how much of each free allowance you've used.
- Expect one small recurring charge regardless: ~$3.60/month for the
  instance's public IPv4 address (see step 5) — not a free-tier item, just
  unavoidable for anything reachable on the public internet.

## 10. Tearing it down

When you're done (or want to stop spending free-tier hours):

```bash
# On the EC2 box, stop the app
docker compose -f docker-compose.free-tier.yml down

# From your machine
aws ec2 terminate-instances --instance-ids <instance-id>
aws ec2 release-address --allocation-id <eip-allocation-id>   # after terminating, or you'll be billed for the idle IP
aws rds delete-db-instance --db-instance-identifier commerceos-db --skip-final-snapshot
aws elasticache delete-cache-cluster --cache-cluster-id commerceos-redis   # if you created it
aws s3 rb s3://commerceos-datasets-<your-account-id> --force
```

## Upgrading later

Outgrown the free tier, or want HTTPS, autoscaling, a real domain, and
managed secrets? That's exactly what [`infra/aws/`](../infra/aws/README.md)
(ECS Fargate + ALB + Secrets Manager + autoscaling) is for — a separate,
production-shaped stack you can move to when the cost tradeoff makes sense,
without needing to redesign the app itself.
