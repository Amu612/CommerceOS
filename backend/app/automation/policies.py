"""
Automation policy engine (ADR 0006).

Maps `(agent, action_type, confidence, severity)` → an execution mode + the role
that must approve it. The guiding rules:

  * Financial actions, price changes, bulk customer communications, and anything
    that changes customer-visible state → NEVER auto. Always NEEDS_APPROVAL or BLOCKED.
  * Low-blast-radius internal actions (flag for review, internal notification,
    queue reprioritisation) → AUTO when confidence is high enough.
  * Everything else → NEEDS_APPROVAL by the owning domain admin.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.models.security import AGENT_ROLE_MAP, UserRole


class Mode(str, Enum):
    AUTO = "AUTO"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    BLOCKED = "BLOCKED"


@dataclass
class Decision:
    mode: Mode
    required_role: str
    reason: str


# action_type -> (base mode, min confidence for AUTO, blocked?)
_ACTION_RULES: dict[str, tuple[Mode, float, bool]] = {
    # low blast radius — auto when confident
    "FLAG_FOR_REVIEW": (Mode.AUTO, 0.55, False),
    "INTERNAL_NOTIFICATION": (Mode.AUTO, 0.50, False),
    "REPRIORITISE_QUEUE": (Mode.AUTO, 0.60, False),
    "ADJUST_PROMISED_DATE_MODEL": (Mode.AUTO, 0.70, False),
    # needs a human in the owning domain
    "CREATE_PURCHASE_ORDER_REQUEST": (Mode.NEEDS_APPROVAL, 1.0, False),
    "REWEIGHT_CARRIER_ROUTING": (Mode.NEEDS_APPROVAL, 1.0, False),
    "OPEN_CARRIER_REVIEW": (Mode.NEEDS_APPROVAL, 1.0, False),
    "SET_MARGIN_FLOOR": (Mode.NEEDS_APPROVAL, 1.0, False),
    "LAUNCH_CAMPAIGN": (Mode.NEEDS_APPROVAL, 1.0, False),
    "ESCALATE_TO_HUMAN": (Mode.NEEDS_APPROVAL, 1.0, False),
    # never automatic regardless of confidence
    "ISSUE_MARKDOWN": (Mode.BLOCKED, 1.0, True),
    "PRICE_CHANGE": (Mode.BLOCKED, 1.0, True),
    "REFUND": (Mode.BLOCKED, 1.0, True),
    "BULK_CUSTOMER_EMAIL": (Mode.BLOCKED, 1.0, True),
    "FUND_TRANSFER": (Mode.BLOCKED, 1.0, True),
}

_DEFAULT_RULE = (Mode.NEEDS_APPROVAL, 1.0, False)


def evaluate(agent: str, action_type: str, *, confidence: float, severity: str) -> Decision:
    base_mode, min_conf, blocked = _ACTION_RULES.get(action_type, _DEFAULT_RULE)
    role = AGENT_ROLE_MAP.get(agent, UserRole.SUPER_ADMIN)
    role_value = role.value if hasattr(role, "value") else str(role)

    if blocked:
        return Decision(Mode.BLOCKED, role_value, f"'{action_type}' is never executed automatically (financial / customer-facing).")

    if base_mode == Mode.AUTO:
        if confidence >= min_conf:
            return Decision(Mode.AUTO, role_value, f"Low-blast-radius action, confidence {confidence:.2f} ≥ {min_conf}.")
        return Decision(Mode.NEEDS_APPROVAL, role_value, f"Confidence {confidence:.2f} below the {min_conf} auto-execute bar.")

    return Decision(Mode.NEEDS_APPROVAL, role_value, f"'{action_type}' requires {role_value} approval.")
