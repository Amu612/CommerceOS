"""Audit log read API (SUPER_ADMIN only). Append-only — no write/delete routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.api.v1.deps import require_super_admin
from app.database.session import get_read_db
from app.models.security import AuditLog, User

router = APIRouter(prefix="/audit", tags=["Audit"])


@router.get("")
def list_audit(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    action: str | None = Query(None),
    actor: str | None = Query(None),
    _: User = Depends(require_super_admin),
    db: Session = Depends(get_read_db),
):
    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    if actor:
        q = q.filter(AuditLog.actor_username == actor)
    total = q.count()
    rows = q.order_by(desc(AuditLog.created_at)).offset(offset).limit(limit).all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": r.id,
                "action": r.action,
                "actor_id": r.actor_id,
                "actor_username": r.actor_username,
                "actor_role": r.actor_role,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "status_code": r.status_code,
                "latency_ms": r.latency_ms,
                "ip_address": r.ip_address,
                "request_id": r.request_id,
                "details": r.details,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }
