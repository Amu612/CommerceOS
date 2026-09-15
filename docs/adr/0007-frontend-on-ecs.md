# ADR 0007 — Serve the frontend from ECS Fargate, not S3 + CloudFront

**Status:** Accepted · **Date:** 2026-09-15

## Context

ADR 0001 and `docs/architecture.md` originally specced the SPA as a static
build in a private S3 bucket behind CloudFront (OAC). Implementing the actual
CI/CD pipeline (`infra/aws/`) surfaced that this was never built: `frontend/`
already ships a real `Dockerfile` (multi-stage Vite build → nginx:alpine,
`nginx.conf` with SPA fallback + immutable asset caching) and
`.github/workflows/deploy.yml` already treated the frontend as a third ECS
service alongside `api`/`worker`, not a static artifact. The S3+CloudFront
path was documentation, not code.

## Decision

Run the frontend as an ECS Fargate service behind the **same ALB** as the
API, routed by path: `/api/*`, `/ws/*`, `/health*`, `/docs`, `/redoc`,
`/openapi.json` → the `api` target group; everything else → the `frontend`
target group serving the nginx container. See `infra/aws/modules/alb` and
`infra/aws/modules/ecs`.

This keeps the stack to one compute platform (Fargate), one image registry
(ECR), one deploy mechanism (`register-task-definition` +
`update-service`), and one health-check model — instead of maintaining two
entirely different release paths (`docker build && ecs update-service` for
the API, `vite build && aws s3 sync && aws cloudfront create-invalidation`
for the SPA) for what is, for this project's traffic level, a small amount
of static content.

## Consequences

- No CDN edge cache or WAF-at-the-edge for the SPA out of the box — traffic
  goes ALB → Fargate for every asset request, not just API calls. At this
  project's scale (see `docs/cost-estimate.md`) that's a non-issue; it would
  matter at meaningfully higher traffic.
- `docs/architecture.md`'s container diagram and `docs/cost-estimate.md`'s
  "CloudFront + S3" line item describe the original plan, not this decision;
  read them alongside this ADR.
- **Reversible without disruption**: CloudFront can be added in front of the
  existing ALB as an additional origin later (cache static asset paths,
  pass `/api/*` and `/ws/*` through) without changing how the frontend is
  built, deployed, or health-checked — it becomes purely an edge layer on
  top of what already exists, not a replacement for it.
