"""
Seed one user per role. Idempotent.

Dev/compose: password = settings.SEED_ADMIN_PASSWORD.
Prod: run once; then rotate passwords and store in Secrets Manager.

    python -m scripts.seed_users
"""

from __future__ import annotations

import sys

from app.core.logging import configure_logging, get_logger
from app.core.settings import settings
from app.database.session import SessionLocal, init_db
from app.models.security import User, UserRole
from app.services.auth_service import create_user

configure_logging()
logger = get_logger("seed_users")

_USERS = [
    ("admin", "admin@commerceos.local", UserRole.SUPER_ADMIN),
    ("orders_admin", "orders@commerceos.local", UserRole.ORDERS_ADMIN),
    ("inventory_admin", "inventory@commerceos.local", UserRole.INVENTORY_ADMIN),
    ("support_admin", "support@commerceos.local", UserRole.CUSTOMER_SUPPORT_ADMIN),
    ("pricing_admin", "pricing@commerceos.local", UserRole.PRICING_ADMIN),
    ("marketing_admin", "marketing@commerceos.local", UserRole.MARKETING_ADMIN),
    ("logistics_admin", "logistics@commerceos.local", UserRole.LOGISTICS_ADMIN),
]


def seed_users() -> int:
    init_db()
    db = SessionLocal()
    created = 0
    try:
        for username, email, role in _USERS:
            if db.query(User).filter(User.username == username).first():
                continue
            create_user(db, username=username, email=email, password=settings.SEED_ADMIN_PASSWORD, role=role)
            created += 1
            logger.info("user_seeded", username=username, role=role.value)
    finally:
        db.close()
    logger.info("seed_users_done", created=created)
    return created


if __name__ == "__main__":
    sys.exit(0 if seed_users() >= 0 else 1)
