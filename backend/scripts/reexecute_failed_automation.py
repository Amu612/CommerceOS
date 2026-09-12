"""
One-time repair: re-executes every FAILED AutomationAction under the fixed
executor (see app/automation/actions.py + executor.py — the SQLite
same-thread-deadlock that caused every one of these to fail has been fixed).

Run once after deploying the fix so the Approvals/Automation console reflects
reality going forward instead of a wall of stale "Failed" rows from before
the bug was found. Safe to re-run: anything still failing (e.g. a handler
with no real fix, or genuinely bad payload data) is left as FAILED with its
new error recorded.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from app.automation.executor import _execute  # noqa: E402
from app.core.logging import get_logger  # noqa: E402
from app.database.session import SessionLocal  # noqa: E402
from app.models.operations import AutomationAction  # noqa: E402

logger = get_logger("scripts.reexecute_failed_automation")


def main() -> None:
    db = SessionLocal()
    try:
        failed = db.query(AutomationAction).filter(AutomationAction.status == "FAILED").all()
        print(f"Found {len(failed)} FAILED automation actions.")
        fixed = 0
        for action in failed:
            before = action.status
            _execute(db, action)
            db.commit()
            after = action.status
            print(f"  {action.id[:8]} {action.action_type:<28} {before} -> {after}")
            if after in ("EXECUTED", "VERIFIED"):
                fixed += 1
        print(f"\n{fixed}/{len(failed)} now succeed.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
