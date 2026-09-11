# ADR 0002 — Primary datastore: PostgreSQL

**Status:** Accepted · **Date:** 2026-09-11

## Context
The current repo ships an 11 MB SQLite file (`orders.db`) committed to git, with
`DATABASE_URL` defaulting to a Windows-path SQLite file. SQLite cannot serve
concurrent Fargate tasks, has no network access, no roles, no replication, and no
managed backups. The analytics (text-to-SQL) agent will also run arbitrary
read-only SQL and needs a real permission model and `statement_timeout`.

## Decision
Use **PostgreSQL 15** everywhere:
- Local dev + CI: Postgres via Docker Compose / a service container.
- Cloud: **RDS PostgreSQL** (Multi-AZ prod), plus a **read replica** and a
  dedicated `commerceos_ro` role (`GRANT SELECT` only) for the analytics agent
  and heavy dashboards.
- Schema is owned by **Alembic** migrations (`alembic upgrade head` as an explicit
  deploy step), not `Base.metadata.create_all` and not a committed binary.
- SQLAlchemy connection pooling with `pool_pre_ping` + `pool_recycle`.
- SQLite remains available only as a zero-setup fallback for unit tests.

## Consequences
- `orders.db` is removed from version control; the database is rebuilt from the
  dataset pipeline (`scripts/build_warehouse.py`).
- All datetime columns are stored timezone-aware; the replay "simulated clock"
  logic is unaffected.
- Two SQLAlchemy engines (`engine`, `read_engine`) and two session factories
  (`SessionLocal`, `ReadSessionLocal`).
