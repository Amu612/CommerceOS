# CommerceOS — Operations Runbook

## Deploy

**Automated (normal path)** — `.github/workflows/deploy.yml`:

- Push to `main` → `dev`. Tag `v*` → `prod` (pauses for the `prod`
  GitHub Environment's required reviewer). `workflow_dispatch` → either, on
  demand.
- Flow: build+push `api`/`frontend` images to ECR (tag `sha-<12-char-sha>`)
  → read that env's config from SSM (`/commerceos/<env>/...`, written by
  Terraform — see `infra/aws/modules/ecs`) → register a new task-definition
  revision per family (`api`, `worker`, `frontend`, `migrate`) with just the
  image swapped → run the one-shot `migrate` task
  (`sync_datasets` + `alembic upgrade head` + `seed_nexus_data` + `seed_users`)
  and wait for it to exit 0 → `ecs update-service --task-definition <new
  revision>` for `api`/`worker`/`frontend` → `ecs wait services-stable` →
  smoke test `GET /health`, `/api/v1/system/health`, `/` → auto-rollback to
  the previous task-def revision on any failure.
- Infra changes (`infra/aws/**`) go through the separate `infra.yml`
  workflow (`terraform plan` on PR, manual `plan`/`apply` via
  `workflow_dispatch`), not `deploy.yml` — see `infra/aws/README.md`
  "Day-to-day: which pipeline does what".

**Manual:**

```bash
env=dev  # or prod
cluster=$(aws ssm get-parameter --name /commerceos/$env/ecs/cluster --query Parameter.Value --output text)
svc_api=$(aws ssm get-parameter --name /commerceos/$env/ecs/service_api --query Parameter.Value --output text)
# ...same for service_worker / service_frontend / migrate_task_family / network/*

cd infra/aws/envs/$env && terraform apply   # evolves task-def shape only, never the live image (see README)
# then either wait for deploy.yml, or hand-roll the register-task-definition +
# run-task + update-service sequence it runs — see its "deploy" job for the exact commands.
```

## Rollback

`deploy.yml` does this automatically on smoke-test failure. By hand:

```bash
# Redeploy the previous task definition for a service
cluster=$(aws ssm get-parameter --name /commerceos/<env>/ecs/cluster --query Parameter.Value --output text)
svc=$(aws ssm get-parameter --name /commerceos/<env>/ecs/service_api --query Parameter.Value --output text)
PREV=$(aws ecs describe-services --cluster "$cluster" --services "$svc" \
  --query 'services[0].deployments[1].taskDefinition' --output text)
aws ecs update-service --cluster "$cluster" --service "$svc" --task-definition "$PREV"
```

- **Migrations:** forward-only. If a migration is bad, ship a new migration that
  corrects it. `alembic downgrade` only in dev.
- **Frontend:** CloudFront serves the last-good `dist`; re-point the S3 sync + create
  an invalidation.

## Database

**Restore (PITR):**

```bash
aws rds restore-db-instance-to-point-in-time \
  --source-db-instance-identifier commerceos-prod \
  --target-db-instance-identifier commerceos-prod-restore \
  --restore-time 2026-09-11T12:00:00Z
# repoint DATABASE_URL secret → new instance → redeploy
```

**Rebuild the warehouse:** re-run the `migrate` task family
(`commerceos-<env>-migrate`, from `aws ssm get-parameter --name /commerceos/<env>/ecs/migrate_task_family`)
via `aws ecs run-task` — idempotent, safe to run again.

**Bootstrap the read-only role** (first apply / after a restore):

```sql
CREATE ROLE commerceos_ro LOGIN PASSWORD '<from secrets manager>';
GRANT CONNECT ON DATABASE commerceos TO commerceos_ro;
GRANT USAGE ON SCHEMA public TO commerceos_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO commerceos_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO commerceos_ro;
```

## Rotate a secret

```bash
aws secretsmanager put-secret-value --secret-id commerceos/<env>/jwt-secret --secret-string "$(openssl rand -hex 32)"
aws ecs update-service --cluster commerceos-<env> --service commerceos-api-<env> --force-new-deployment
```

Rotating `jwt-secret` invalidates all sessions — announce it.

## Scale

- **api:** autoscales on CPU + ALB RequestCountPerTarget. Manual floor/ceiling:
  `aws application-autoscaling register-scalable-target ...`.
- **worker:** stays at 1 (single replay writer; Redis lock enforces it). Do not scale.
- **RDS/Redis:** `terraform apply` with a larger instance class (Multi-AZ minimises downtime).

## Replay engine ops

- Status: `GET /api/v1/simulation/status`.
- Control (SUPER_ADMIN): `POST /api/v1/simulation/control {action: start|pause|resume|stop|reset|step}`.
- `reset` **clears order tables** — confirm intent; it is audited.
- If the worker dies mid-run it resumes from the Redis-persisted position on restart.
- "Replay lag" alarm high ⇒ worker CPU-bound or DB write contention; check worker logs.

## Incident triage

| Symptom (alarm)       | Likely cause                        | First checks                                                                     |
| --------------------- | ----------------------------------- | -------------------------------------------------------------------------------- |
| 5xx rate up           | bad deploy / DB down / Redis down   | `/api/v1/health/ready`, recent deploy, RDS + Redis CloudWatch                    |
| p95 latency up        | DB slow queries / LLM provider slow | X-Ray slowest spans, `pg_stat_activity`, LLM circuit-breaker metric              |
| agent-failure rate up | LLM provider errors / data gap      | agent logs (`execution_id`), `LLM_PROVIDER` — flip to `deterministic` to isolate |
| LLM $/day alarm       | runaway loop / abuse                | `llm_tokens_total` by agent, rate-limit + budget config, check for a retry storm |
| replay lag            | worker stuck                        | worker logs, Redis lock key, restart worker task                                 |
| DB connections maxed  | pool too small / leak               | `DB_POOL_SIZE`, look for un-closed sessions                                      |

## First-time repo hygiene

Before making the repo public, scrub the historically-committed env file:

```bash
pip install git-filter-repo
git filter-repo --path backend/.env --invert-paths --force
git push --force-with-lease
```

Then rotate anything that was ever in it.
