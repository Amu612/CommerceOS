# CommerceOS — AWS infrastructure (Terraform)

Provisions the whole platform — network, both databases, compute, and the
CI/CD wiring around it — for two environments, `dev` and `prod`.

```
infra/aws/
├── bootstrap/       one-time, run by hand: tfstate bucket + lock table + GitHub OIDC role
├── modules/
│   ├── network/         VPC, public/private subnets, NAT, VPC endpoints, flow logs
│   ├── ecr/              container registries (api, frontend)
│   ├── datasets/          S3 bucket for the Olist + DataCo CSVs (gitignored, not in the image)
│   ├── secrets/            generic Secrets Manager key/value store
│   ├── rds/                 PostgreSQL (Multi-AZ + read replica optional)
│   ├── redis/                ElastiCache Redis (encrypted, auth token in prod)
│   ├── alb/                   ALB, target groups, HTTP(S) listener + routing rules
│   ├── ecs/                    cluster, api/worker/frontend/migrate task defs + services, autoscaling, IAM, SSM config
│   └── observability/          SNS + CloudWatch alarms + dashboard
└── envs/
    ├── dev/              single-AZ, t4g.micro, docs on, auth soft-enforced
    └── prod/             Multi-AZ + replica, bigger instances, docs off, auth enforced
```

## Architecture (what actually gets created)

```
                         ┌──────────────── VPC ────────────────────────────┐
Internet ── ALB (public subnets) ──┬──▶ ECS Fargate: frontend (Nginx SPA)   │
                                    └──▶ ECS Fargate: api (FastAPI)         │
                                         ECS Fargate: worker (replay loop)  │
                                              │        │                    │
                                    (private subnets)  │                    │
                                         RDS PostgreSQL │ ElastiCache Redis  │
                                    └───────────────────┴────────────────────┘
        S3: datasets (Olist/DataCo CSVs) ── read by the one-shot `migrate` task
        S3/DynamoDB (separate, in `bootstrap/`): Terraform remote state
        ECR: commerceos/api, commerceos/frontend
        Secrets Manager: database-url, redis-url, jwt-secret, seed-admin-password, LLM keys
```

One deliberate simplification vs. the original `docs/architecture.md` sketch
(S3 + CloudFront for the SPA): the frontend is a container (see
`frontend/Dockerfile` + `nginx.conf`) running on ECS Fargate behind the same
ALB as the API, routed by path (`/api/*`, `/ws/*`, `/health*`, `/docs` →
api target group; everything else → frontend target group). One compute
platform, one deploy mechanism, no S3 sync/invalidation step — see
`docs/adr/0007-frontend-on-ecs.md`. CloudFront can be layered in front of the
ALB later purely as a cache/WAF edge without re-architecting anything here.

## Prerequisites

- Terraform ≥ 1.7, AWS CLI v2, an AWS account with an admin identity for the
  one-time bootstrap step below.
- *(Recommended, optional)* a domain + ACM certificate for HTTPS. Without one,
  the ALB serves the app over plain HTTP at its own `*.elb.amazonaws.com`
  DNS name — fine for a first deploy, not for real traffic.

## First-time setup (once per AWS account)

1. **Bootstrap remote state + the GitHub OIDC role** — see
   `infra/aws/bootstrap/README.md`. This creates the S3/DynamoDB Terraform
   backend and two IAM roles Actions assumes via OIDC (no static AWS keys
   anywhere): `commerceos-deploy` (image build/push + ECS rollout) and
   `commerceos-terraform` (plan/apply this Terraform).

2. **Point each env at that backend**:
   ```bash
   cp infra/aws/envs/dev/backend.hcl.example infra/aws/envs/dev/backend.hcl
   cp infra/aws/envs/prod/backend.hcl.example infra/aws/envs/prod/backend.hcl
   # fill in the bucket/table names from the bootstrap step's outputs
   ```

3. **Push a bootstrap image.** The `ecs` module's task definitions need a
   *real* image reference to create successfully — there's no image in ECR
   yet on a brand-new account, and `terraform apply` can't create a task
   definition pointing at nothing. Do this once, before the first apply:
   ```bash
   aws ecr create-repository --repository-name commerceos/api      || true
   aws ecr create-repository --repository-name commerceos/frontend || true
   aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com

   docker build -t <account-id>.dkr.ecr.us-east-1.amazonaws.com/commerceos/api:bootstrap backend
   docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/commerceos/api:bootstrap

   docker build -t <account-id>.dkr.ecr.us-east-1.amazonaws.com/commerceos/frontend:bootstrap frontend \
     --build-arg VITE_API_BASE_URL=http://placeholder --build-arg VITE_WS_BASE_URL=ws://placeholder
   docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/commerceos/frontend:bootstrap
   ```
   (`image_tag = "bootstrap"` is already the default in both envs'
   `terraform.tfvars.example`.) After the first real deploy via `deploy.yml`,
   this step never needs repeating — CI registers new task-def revisions
   from then on.

4. **Upload the datasets once per environment** — they're gitignored
   (~194 MB, see `docs/gap-analysis.md`) so they aren't in the repo or the
   image. The `migrate` task pulls them from S3 at deploy time:
   ```bash
   aws s3 sync backend/data/raw/ s3://$(terraform -chdir=infra/aws/envs/dev output -raw datasets_bucket)/raw/
   ```

5. **First apply**:
   ```bash
   cd infra/aws/envs/dev
   cp terraform.tfvars.example terraform.tfvars   # fill in domain/cert/LLM key if you have them
   terraform init -backend-config=backend.hcl
   terraform apply
   ```
   Repeat for `envs/prod` when ready (`prod` additionally wants
   `alert_email` set and, strongly, a real ACM cert).

6. **GitHub repo config** (Settings → Secrets and variables → Actions), from
   the bootstrap `terraform output`:
   - Secrets: `AWS_DEPLOY_ROLE_ARN`, `AWS_TERRAFORM_ROLE_ARN`, and (optional)
     `GROQ_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` if you want CI's
     `infra.yml` to manage those.
   - Variables: `TF_STATE_BUCKET`, `TF_STATE_LOCK_TABLE`, and optionally
     `DOMAIN_NAME`, `ACM_CERTIFICATE_ARN`, `ALERT_EMAIL`, `LLM_PROVIDER`.
   - Environments: create `dev` and `prod`; add a required reviewer on `prod`
     so both a `v*` tag push (`deploy.yml`) and a prod `terraform apply`
     (`infra.yml`) pause for approval.

From here on, everything is automatic — see the root `README.md`'s CI/CD
section and `docs/runbook.md`.

## Day-to-day: which pipeline does what

| Change | Pipeline | What happens |
|---|---|---|
| App code (`backend/`, `frontend/`) | `.github/workflows/ci.yml` → `deploy.yml` | Build+push images, run the `migrate` task, roll `api`/`worker`/`frontend` forward, smoke test, auto-rollback on failure |
| Infra (`infra/aws/**`) | `.github/workflows/infra.yml` | PR → `terraform plan` posted as a PR comment. Manual dispatch → `plan` or `apply` to `dev`/`prod` (prod gated by the environment's required reviewer) |

Terraform and the deploy pipeline deliberately don't fight over the running
image: every ECS *service* has `lifecycle.ignore_changes = [task_definition]`,
so `terraform apply` can freely evolve a task definition's shape (cpu/memory,
env vars, secrets, IAM) without silently reverting whichever image tag
`deploy.yml` last shipped. `deploy.yml` registers new task-definition
*revisions* from whatever Terraform last defined, swaps only the image, and
points the service at that new revision — see the `register-task-definition`
step in `deploy.yml` and `docs/runbook.md`.

## Databases

- **PostgreSQL (RDS)**: schema is owned by Alembic (`backend/alembic/`), not
  Terraform — the `migrate` ECS task runs `alembic upgrade head` on every
  deploy. Terraform only provisions the instance, storage, backups, and (prod)
  a read replica for analytics/heavy dashboards.
- **Redis (ElastiCache)**: event bus, cache, rate limiting, and the replay
  engine's authoritative simulation state. No schema to migrate.
- Both live in private subnets only — `publicly_accessible = false`, ingress
  restricted to the VPC CIDR, no route to the internet gateway.

## Secrets

Terraform generates `jwt-secret` and `seed-admin-password` (via `random_password`)
and composes `database-url` / `redis-url` from the RDS/ElastiCache modules'
own outputs, then writes all of it to Secrets Manager once. After that, every
secret is `lifecycle.ignore_changes`'d on its value — rotate with
`aws secretsmanager put-secret-value` (see `docs/runbook.md`) and Terraform
will never overwrite your rotation on the next apply.

## Cost

Dev is sized to the `dev` scenario in `docs/cost-estimate.md` (~$140-150/mo
on-demand, less if you stop it out of hours). Prod matches the `prod-low`
sizing there. Both are start-here defaults, not fixed — resize freely via
each env's `main.tf`.
