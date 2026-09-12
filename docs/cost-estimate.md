# CommerceOS — AWS Cost Estimate

**Region:** us-east-1 · **Pricing date:** 2026-09 (on-demand list; verify before commit)
**Method:** line-item monthly (730 hrs). Numbers are planning estimates, not a quote.
The editable model is `docs/cost-model.csv` — change the assumptions there and
re-total. A `terraform`-driven `infracost diff` runs on every `infra/**` PR (task I9)
and should agree within ±15%.

---

## Scenario A — `dev` (always-on, minimal)

| Service                                          | Config                                    |     Est. $/mo |
| ------------------------------------------------ | ----------------------------------------- | ------------: |
| ECS Fargate — api                                | 1 task × 0.5 vCPU / 1 GB × 730h           |            18 |
| ECS Fargate — worker                             | 1 task × 0.25 vCPU / 0.5 GB × 730h        |             9 |
| RDS PostgreSQL                                   | `db.t4g.micro` single-AZ, 20 GB gp3       |            15 |
| ElastiCache Redis                                | `cache.t4g.micro` single node             |            12 |
| ALB                                              | 1 ALB + minimal LCUs                      |            18 |
| NAT Gateway                                      | 1 × 730h + ~10 GB                         |            34 |
| CloudFront + S3                                  | low traffic, <10 GB egress                |             3 |
| Secrets Manager                                  | 6 secrets                                 |             2 |
| CloudWatch                                       | logs ~5 GB + metrics + dashboard          |             8 |
| ECR                                              | 2 repos, ~2 GB                            |             1 |
| Route53                                          | 1 hosted zone                             |             1 |
| **Subtotal (infra)**                             |                                           |      **~143** |
| LLM (dev, deterministic or light OpenAI dev key) | ~2k agent runs/mo × 1.5k tok, gpt-4o-mini |             3 |
| **Dev total**                                    |                                           | **~146 / mo** |

> Cut to ~$70/mo by scheduling the ECS services + RDS to stop overnight/weekends
> and using 1 NAT-less setup with VPC endpoints only for a pure-internal dev.

---

## Scenario B — `prod-low` (early production, light load)

Assumptions: ~50k API req/day, ~15 scheduled agent runs/hr (6 agents + orchestrator),
~500 analytics NL queries/day, ~2k customer-support turns/day.

| Service               | Config                                         | Est. $/mo |
| --------------------- | ---------------------------------------------- | --------: |
| ECS Fargate — api     | 2 tasks × 1 vCPU / 2 GB × 730h                 |       145 |
| ECS Fargate — worker  | 1 task × 0.5 vCPU / 1 GB × 730h                |        18 |
| RDS PostgreSQL        | `db.t4g.medium` Multi-AZ, 100 GB gp3           |       190 |
| RDS read replica      | `db.t4g.medium` single-AZ                      |        70 |
| ElastiCache Redis     | `cache.t4g.small` primary + replica            |        55 |
| ALB                   | + moderate LCUs                                |        25 |
| NAT Gateway           | 2 AZ × 730h + ~50 GB                           |        75 |
| CloudFront + S3 + WAF | ~100 GB egress + WAF (2 ACLs, rules, requests) |        25 |
| Secrets Manager       | 6 secrets + API calls                          |         3 |
| CloudWatch + X-Ray    | ~40 GB logs, custom metrics, traces, alarms    |        45 |
| ECR                   | 2 repos, image history                         |         3 |
| Backups / snapshots   | RDS PITR + snapshots ~150 GB                   |        15 |
| **Subtotal (infra)**  |                                                |  **~668** |

### LLM cost (prod-low), by provider

Token model per call type (prompt + completion, rough):

| Call                                                      | tokens | calls/day |
| --------------------------------------------------------- | -----: | --------: |
| domain-agent triage/synthesis                             |  1,800 |    ~2,200 |
| orchestrator correlation/conflict                         |  3,000 |      ~350 |
| analytics NL→SQL (plan+gen+summarise, ~2 passes)          |  4,500 |      ~500 |
| customer support turn (router + specialists + supervisor) |  3,500 |    ~2,000 |

≈ **19.5M tokens/day ≈ 585M tokens/mo**, ~35% completion.

| Provider (model)          | blended $/1M |     $/mo |
| ------------------------- | -----------: | -------: |
| Bedrock Claude 3.5 Haiku  |         ~1.3 | **~760** |
| OpenAI gpt-4o-mini        |        ~0.35 |     ~205 |
| Bedrock Claude 3.5 Sonnet |           ~7 |   ~4,100 |

**Recommended prod-low mix:** Haiku/gpt-4o-mini class for triage + customer turns,
Sonnet only for the orchestrator + analytics summarisation ⇒ **~$450/mo LLM**.

**`prod-low` total ≈ $668 infra + $450 LLM ≈ ~$1,120 / mo** (+20% buffer ⇒ **~$1,350**).

---

## Scenario C — `prod-target` (steady production)

~5× the traffic of `prod-low`, api autoscaled to avg 4 tasks, `db.r6g.large`
Multi-AZ + replica, `cache.r6g.large`.

| Bucket                                               |                                    Est. $/mo |
| ---------------------------------------------------- | -------------------------------------------: |
| Compute (ECS api avg 4 + worker)                     |                                         ~650 |
| RDS (r6g.large Multi-AZ + replica) + storage/backups |                                         ~750 |
| Redis (r6g.large + replica)                          |                                         ~330 |
| ALB + NAT + data transfer                            |                                         ~220 |
| CloudFront + WAF + S3                                |                                          ~90 |
| Observability (CloudWatch + X-Ray)                   |                                         ~140 |
| Secrets / ECR / Route53                              |                                          ~15 |
| **Infra subtotal**                                   |                                   **~2,195** |
| LLM (recommended mix, ~2.9B tok/mo)                  |                                       ~2,200 |
| **prod-target total**                                | **~$4,400 / mo** (+20% buffer ⇒ **~$5,300**) |

---

## Cost controls (task I9 / OB5)

- **AWS Budgets** monthly + anomaly detection → SNS.
- **LLM budget alarm** at `LLM_MONTHLY_BUDGET_USD`; the guardrail refuses calls
  once a per-request token budget is exceeded and the daily rollup is over budget.
- **Fargate**: right-size from CloudWatch, use Fargate Spot for the worker.
- **RDS/Redis**: 1-yr reserved / savings plan once steady ⇒ ~30–40% off compute.
- **NAT**: the biggest surprise line — VPC endpoints already cover AWS API traffic;
  consider a single NAT in `prod` if cross-AZ egress is tolerable.
- **CloudWatch**: 30-day log retention in dev, ship older to S3 in prod;
  sample X-Ray at 5–10%.
- **Dev**: stop ECS + RDS out of hours (EventBridge schedule) ⇒ ~50% off dev.

---

## What is NOT in these numbers

- One-time: dataset download/transfer, ACM (free), Route53 domain registration.
- Cross-account / multi-region DR.
- Support plan (Developer ~$29/mo or 3% of spend recommended for prod).
- CI minutes (GitHub Actions — free tier likely sufficient early).
