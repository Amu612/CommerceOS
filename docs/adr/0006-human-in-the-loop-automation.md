# ADR 0006 — Human-in-the-loop automation with a policy gate

**Status:** Accepted · **Date:** 2026-09-11

## Context
Agents produce recommendations (flag a backlog, issue a markdown, create a PO,
escalate a cancellation review). Some are safe to auto-execute; some move money
or change customer-visible state and must not run without a human. The current
code emits `automation_eligibility` metadata but nothing consumes it.

## Decision
`app/automation/` with a **policy engine** mapping
`(agent, action_type, confidence)` → one of:
- `AUTO` — executed immediately by the executor, written to `automation_actions`,
  then **verified** (re-observe to confirm the intended effect) and **rollback-able**.
- `NEEDS_APPROVAL(role)` — queued in `approvals` for a human with the matching
  admin role (or `SUPER_ADMIN`); executed only after approval; every state change audited.
- `BLOCKED` — never executed automatically (e.g. any fund transfer, any price
  change above a threshold, any bulk customer communication).

Financial actions and bulk external communications are **always** `BLOCKED` or
`NEEDS_APPROVAL` regardless of confidence.

## Consequences
- A dedicated Approvals UI + `/api/v1/approvals` API.
- The executor is idempotent and records enough to roll back.
- Auto-executed actions are low-blast-radius only (flags, internal notifications,
  queue reprioritisation).
- Responsible-AI posture is documented in `docs/security.md`.
