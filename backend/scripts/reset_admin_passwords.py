"""
One-time: resets every seeded admin's password to `settings.SEED_ADMIN_PASSWORD`.

`seed_users.py` is idempotent — it skips a user that already exists, so it
never rotates a password. This is the explicit "rotate now" companion for
when that password changes (e.g. after updating SEED_ADMIN_PASSWORD).

    python -m scripts.reset_admin_passwords
"""

from __future__ import annotations

import sys

from app.core.logging import configure_logging, get_logger
from app.core.security import hash_password
from app.core.settings import settings
from app.database.session import SessionLocal
from app.models.security import User

configure_logging()
logger = get_logger("reset_admin_passwords")

_USERNAMES = [
    "admin",
    "orders_admin",
    "inventory_admin",
    "support_admin",
    "pricing_admin",
    "marketing_admin",
    "logistics_admin",
]


def reset_admin_passwords() -> int:
    db = SessionLocal()
    updated = 0
    try:
        new_hash = hash_password(settings.SEED_ADMIN_PASSWORD)
        for username in _USERNAMES:
            user = db.query(User).filter(User.username == username).first()
            if not user:
                logger.warning("user_not_found", username=username)
                continue
            user.hashed_password = new_hash
            updated += 1
            logger.info("password_reset", username=username, role=user.role.value)
        db.commit()
    finally:
        db.close()
    logger.info("reset_admin_passwords_done", updated=updated)
    return updated


if __name__ == "__main__":
    sys.exit(0 if reset_admin_passwords() >= 0 else 1)
