# CommerceOS — Security

## 1. Threat model (STRIDE-lite)

| Threat                        | Vector                                             | Mitigation                                                                                                                                                                                                                                                                  |
| ----------------------------- | -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Spoofing**                  | forged / replayed token                            | Short-lived HS256 JWT (12h), signature verify, `type` claim check, unknown `sub` ⇒ 401 (no fabricated users), refresh token separate.                                                                                                                                       |
| **Tampering**                 | request/response tamper, SQL injection             | HTTPS only; pydantic validation + body-size cap; analytics agent uses a read-only role + AST allow-list (SELECT/WITH only) + `statement_timeout` + row cap + single statement.                                                                                              |
| **Repudiation**               | "I didn't do that"                                 | Append-only `audit_logs` (one row per authenticated mutation), no update/delete route, shipped to CloudWatch, `request_id` correlates logs ↔ traces ↔ audit.                                                                                                                |
| **Information disclosure**    | stack traces, PII in logs, secrets in repo         | RFC-7807 errors with generic body in prod; PII scrub before any prompt is logged; secrets only in Secrets Manager; `detect-secrets` in CI + pre-commit; `/docs` off in prod.                                                                                                |
| **Denial of service**         | request flood, expensive LLM calls, cross-join SQL | Redis token-bucket rate limit (per-IP + per-user, stricter on `/auth/login` + LLM endpoints); LLM per-request token budget + circuit breaker; SQL statement timeout; ECS autoscaling; WAF rate rule.                                                                        |
| **Elevation of privilege**    | role bypass                                        | RBAC dependency on every route; agent controls / simulation / user admin require the matching admin or `SUPER_ADMIN`; contract test asserts 401 without a token on every route.                                                                                             |
| **Prompt injection**          | untrusted text steering the LLM/agent              | Injection-marker stripping on all untrusted input before it reaches the model; the LLM cannot execute tools directly — it only classifies intent / synthesises text / (analytics) proposes SQL that is then guarded; structured outputs validated against pydantic schemas. |
| **Autonomous harmful action** | agent auto-executes something damaging             | Policy engine: financial actions & bulk customer comms are always `BLOCKED` / `NEEDS_APPROVAL` regardless of confidence; auto actions are low-blast-radius only (flags, internal notifications) and are verified + rollback-able.                                           |

## 2. RBAC matrix

Roles: `SUPER_ADMIN`, `ORDERS_ADMIN`, `INVENTORY_ADMIN`, `CUSTOMER_SUPPORT_ADMIN`,
`PRICING_ADMIN`, `MARKETING_ADMIN`, `LOGISTICS_ADMIN`.

| Route group                                           | SUPER_ADMIN | Domain admin (own)                                   | Other authenticated           |
| ----------------------------------------------------- | ----------- | ---------------------------------------------------- | ----------------------------- | --- | --------- |
| `GET /health*`, `POST /api/v1/auth/login`             | ✅ public   | ✅ public                                            | ✅ public                     |
| `GET /api/v1/agents/\*/analyze                        | runs        | findings`, `GET /dashboard/\*`, `GET /notifications` | ✅                            | ✅  | ✅ (read) |
| `POST /api/v1/agents/<d>/*` control/mutations         | ✅          | ✅ for `<d>`                                         | ❌ 403                        |
| `POST /api/v1/simulation/*` (replay start/stop/reset) | ✅          | ❌                                                   | ❌                            |
| `POST /api/v1/orchestrator/run`                       | ✅          | ❌                                                   | ❌                            |
| `POST /api/v1/approvals/{id}/approve                  | reject`     | ✅                                                   | ✅ if `required_role` matches | ❌  |
| `GET/POST /api/v1/users`, `GET /api/v1/audit`         | ✅          | ❌                                                   | ❌                            |

## 3. PII inventory & handling

| Data                                 | Where                        | Classification                       | Handling                                                                                                              |
| ------------------------------------ | ---------------------------- | ------------------------------------ | --------------------------------------------------------------------------------------------------------------------- |
| Olist customer city/state/zip-prefix | `customers`, `dataco_orders` | pseudonymous (dataset is anonymised) | retained; used for geo analysis only                                                                                  |
| Platform user email + name           | `users`                      | PII                                  | bcrypt password; email not logged; deletion = deactivate + scrub on request                                           |
| Audit actor + IP                     | `audit_logs`                 | PII (IP)                             | retained 400 days (prod) then purged; access = SUPER_ADMIN only                                                       |
| Customer support conversation text   | `conversation_messages`      | may contain PII                      | PII scrub before any LLM log; retention 180 days; `customer_memory` stores only derived preferences, not raw messages |
| LLM prompts/completions              | logs/traces                  | may contain PII                      | scrubbed (`scrub_pii`) before emission; traces sampled                                                                |

- **At rest:** RDS + ElastiCache + S3 encrypted (KMS). Automated RDS backups + PITR.
- **In transit:** TLS 1.2+ everywhere (ACM); Redis auth token + in-transit encryption.
- **Retention jobs:** worker housekeeping prunes `agent_runs`, resolved
  `notifications`, old `audit_logs`, expired `conversations` on schedule.

## 4. Secret management

| Secret                                             | Store           | Rotation                                   |
| -------------------------------------------------- | --------------- | ------------------------------------------ |
| DB credentials (`commerceos_app`, `commerceos_ro`) | Secrets Manager | 90 days (RDS-managed rotation)             |
| `JWT_SECRET`                                       | Secrets Manager | manual, coordinated (invalidates sessions) |
| Redis auth token                                   | Secrets Manager | 90 days                                    |
| LLM API keys (if not Bedrock)                      | Secrets Manager | per-vendor policy                          |

- Nothing sensitive in the repo. `.env` is gitignored; `.env.example` documents keys with placeholders.
- The previously-committed `backend/.env` has been removed; scrub it from git
  history with `git filter-repo --path backend/.env --invert-paths` before making
  the repo public (see `docs/runbook.md`).
- CI: `detect-secrets scan --baseline .secrets.baseline` + `gitleaks`.

## 5. Dependency & image security

- `pip-audit` (backend) + `npm audit` (frontend) + Dependabot on `main`.
- Trivy scan on every built image + ECR enhanced scanning.
- Base images pinned (`python:3.11-slim`, `nginx:1.27-alpine`); non-root containers.

## 6. Responsible AI

- The LLM never performs arithmetic or takes an action directly — it classifies,
  summarises, or proposes SQL that is independently validated.
- Every agent output carries a `data_status` provenance tag and a sample-size-aware
  confidence; `NOT_ESTIMABLE` is returned rather than a guess when data is insufficient.
- No autonomous financial transactions. Markdowns, POs, bulk communications, and
  refunds above a threshold require human approval with an audit trail.
- Analytics agent refuses out-of-scope questions instead of hallucinating.

## 7. Incident response (summary — full flow in runbook)

1. Alarm → SNS → on-call.
2. Triage via CloudWatch dashboard (5xx, p95, agent-failure, replay lag, DB/Redis).
3. Contain: scale, disable a bad agent (`LLM_PROVIDER=deterministic` or feature flag), rotate a leaked secret.
4. Rollback: `deploy.yml` auto-rolls back on failed smoke; manual = redeploy previous task def.
5. Post-incident: audit-log + trace review, ADR/runbook update.
