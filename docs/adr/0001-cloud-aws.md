# ADR 0001 — Cloud platform: AWS

**Status:** Accepted · **Date:** 2026-09-11

## Context
The problem statement requires the platform to be "fully deployed on a cloud platform
(AWS preferred)". The reference repositories use GCP (Cloud Run + Cloud SQL + Terraform).
We need managed containers, a managed relational DB, a managed cache, static hosting for
the SPA, secret management, and an LLM service.

## Decision
Target **AWS**. Concretely:

| Concern | Service |
|---|---|
| Container orchestration | ECS **Fargate** behind an **ALB** (api service, autoscaled; worker service, single replica) |
| Relational DB | **RDS PostgreSQL** (Multi-AZ in prod) + a read replica for analytics |
| Cache / event bus / rate-limit / replay state | **ElastiCache Redis** |
| SPA hosting | **S3** (private) + **CloudFront** (OAC) |
| Images | **ECR** |
| Secrets / config | **Secrets Manager** + **SSM Parameter Store** |
| LLM | **Amazon Bedrock** (Claude / Titan) primary; OpenAI/Anthropic as env-selectable fallback |
| Observability | **CloudWatch** logs/metrics/alarms + **X-Ray** via ADOT |
| Edge security | **WAF** on ALB and CloudFront; **ACM** TLS |
| IaC | **Terraform** (`infra/aws/`), remote state in S3 + DynamoDB lock |
| CI/CD | **GitHub Actions** → ECR → ECS (OIDC, no static keys) |

App Runner was considered for the api tier (simpler) but rejected: we need a
long-running non-HTTP worker, VPC-internal RDS/Redis, and fine-grained autoscaling
that ECS gives uniformly for both services.

## Consequences
- One VPC with public/private subnets; RDS and Redis never publicly routable.
- Bedrock keeps model traffic inside AWS (no third-party key in prod by default).
- GCP Terraform from the reference repos is adapted, not reused.
- Slightly more infra surface than App Runner, but a single consistent model.
