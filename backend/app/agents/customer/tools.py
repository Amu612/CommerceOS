"""
Customer Support Agent Tools.

Every tool reads the SAME data source as the Orders and Inventory agents:
the seeded `orders.db` (Olist + DataCo transaction tables) through SQLAlchemy,
strictly source-aware to the replay engine's simulated clock T. No external
product API, no separate datastore.

These are thin, customer-facing wrappers around `OrdersTools` plus a billing
helper that reads real payment records.
"""
import re
import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.models.olist import Order, OrderPayment
from app.models.dataco import DataCoOrder
from app.agents.orders.tools import OrdersTools, get_simulated_clock

logger = logging.getLogger(__name__)

# 32-char Olist UUID, a "#1234" style id, or a bare 2-10 digit DataCo id
_ORDER_ID_RE = re.compile(r"#?\b([0-9a-fA-F]{32}|\d{2,10})\b")

_PRODUCT_STOPWORDS = {
    "do", "you", "have", "any", "a", "an", "the", "in", "stock", "for", "me", "is",
    "are", "there", "of", "to", "i", "want", "need", "buy", "purchase", "please",
    "show", "find", "get", "whats", "what", "s", "your", "some", "can", "tell",
    "about", "price", "cost", "much", "how", "available", "availability",
}


def extract_order_id(message: str) -> str:
    """Pulls the most likely order identifier out of a free-text message."""
    if not message:
        return ""
    # Prefer an explicit "order <id>" / "order #<id>" / "invoice <id>" mention
    m = re.search(
        r"(?:order|invoice|rma|tracking)\s*(?:id|number|no\.?|#)?\s*[:#]?\s*([0-9a-fA-F]{4,32}|\d{1,10})",
        message,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    m = _ORDER_ID_RE.search(message)
    return m.group(1) if m else ""


def clean_product_query(message: str) -> str:
    """Reduces a free-text sales question to its most likely product keyword(s)."""
    if not message:
        return ""
    # Keep an explicit product id if present
    pid = re.search(r"\b([0-9a-fA-F]{32}|\d{3,10})\b", message)
    if pid:
        return pid.group(1)
    words = re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", message.lower())
    kept = [w for w in words if w not in _PRODUCT_STOPWORDS]
    return " ".join(kept[-3:]) if kept else message


def _fmt_date(value: Optional[str], fallback: str = "on schedule") -> str:
    if not value:
        return fallback
    v = str(value)
    return v[:10] if re.match(r"\d{4}-\d{2}-\d{2}", v) else v


def verify_order_id(order_id: str, db: Optional[Session] = None) -> bool:
    """Confirms an id resolves to a real Olist or DataCo order."""
    if not order_id:
        return False
    close = False
    if db is None:
        db = SessionLocal()
        close = True
    try:
        clean = str(order_id).strip().replace("#", "")
        if db.query(Order.order_id).filter(Order.order_id.ilike(f"{clean}%")).first():
            return True
        try:
            if db.query(DataCoOrder.order_id).filter(DataCoOrder.order_id == int(clean)).first():
                return True
        except ValueError:
            pass
        return False
    finally:
        if close:
            db.close()


class CustomerSupportTools:
    """
    Stateless customer-facing toolset. Each method returns a (text, tool_records)
    tuple so the graph can both synthesise an answer and surface the raw trace.
    """

    # ── Context ───────────────────────────────────────────────────
    @staticmethod
    def order_context(message: str, db: Optional[Session] = None) -> Tuple[str, List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Resolves any order referenced in the message into a structured context block."""
        order_id = extract_order_id(message)
        if not order_id:
            return ("No order identifier was referenced in the conversation.", [], None)

        detail = OrdersTools.lookup_order(order_id, db=db)
        rec = {
            "tool": "tool_lookup_order",
            "name": "lookup_order",
            "input": {"order_id": order_id},
        }
        if not detail:
            rec["output"] = f"Order #{order_id} not found in transaction records."
            return (rec["output"], [rec], None)

        ctx = {
            "order_id": detail.order_id,
            "status": detail.status,
            "total": detail.total,
            "customer_city": detail.customer_city,
            "customer_state": detail.customer_state,
            "purchase_date": (detail.purchase_timestamp or "")[:10],
            "delivered_date": (detail.delivered_customer_date or "")[:10] or None,
            "estimated_delivery": (detail.estimated_delivery_date or "")[:10] or None,
            "tracking_number": detail.tracking_number,
            "item_count": len(detail.items),
        }
        rec["output"] = ctx
        text = (
            f"Order #{ctx['order_id']} | status={ctx['status']} | total=${ctx['total']:.2f} | "
            f"placed {ctx['purchase_date']} | ships to {ctx['customer_city'] or '?'}, {ctx['customer_state'] or '?'} | "
            f"{ctx['item_count']} item(s) | tracking {ctx['tracking_number']}"
        )
        return (text, [rec], ctx)

    # ── Support / shipping ────────────────────────────────────────
    @staticmethod
    def shipment_status(order_id: str, db: Optional[Session] = None) -> Tuple[str, List[Dict[str, Any]]]:
        if not order_id:
            return ("Ask the customer for an Order ID so the shipment can be located.", [])
        res = OrdersTools.track_shipment(order_id, db=db)
        rec = {
            "tool": "tool_track_shipment",
            "name": "track_shipment",
            "input": {"order_id": order_id},
            "output": res.model_dump(),
        }
        if res.status == "NOT_FOUND":
            return (f"Shipment for order #{order_id} could not be found.", [rec])
        milestones = "; ".join(f"{e.status} @ {e.location} ({_fmt_date(e.timestamp)})" for e in res.events) or "registered with carrier"
        text = (
            f"Carrier {res.carrier} | tracking {res.tracking_number} | status {res.status} | "
            f"ETA {_fmt_date(res.estimated_delivery)}. Milestones: {milestones}."
        )
        return (text, [rec])

    # ── Sales / products ──────────────────────────────────────────
    @staticmethod
    def product_info(keyword: str, db: Optional[Session] = None) -> Tuple[str, List[Dict[str, Any]]]:
        if not keyword:
            return ("Ask the customer which product, category, or product ID they are interested in.", [])
        # lookup_product tokenises internally (whole phrase + each noun, PT + EN categories).
        res = OrdersTools.lookup_product(keyword, db=db)
        rec = {
            "tool": "tool_lookup_product",
            "name": "lookup_product",
            "input": {"product_id_or_keyword": keyword},
            "output": res,
        }
        if res.get("status") != "OK" or not res.get("products"):
            return (f"No catalog match for '{keyword}'.", [rec])
        lines = []
        for p in res["products"][:3]:
            cat = p.get("category_english") or p.get("category", "General")
            price = p.get("avg_price", p.get("price", 0.0))
            lines.append(f"{cat.title()} (ID {str(p.get('product_id'))[:8]}) ~ ${price:.2f}, {p.get('orders_count', 0)} sold")
        return ("Catalog matches: " + " | ".join(lines), [rec])

    # ── Billing ───────────────────────────────────────────────────
    @staticmethod
    def billing_lookup(order_id: str, db: Optional[Session] = None) -> Tuple[str, List[Dict[str, Any]]]:
        if not order_id:
            return ("Ask the customer for an Order or Invoice ID to look up the charge.", [])
        close = False
        if db is None:
            db = SessionLocal()
            close = True
        try:
            clean = str(order_id).strip().replace("#", "")
            rec = {"tool": "billing_lookup", "name": "billing_lookup", "input": {"order_id": order_id}}

            payments = (
                db.query(OrderPayment)
                .filter(OrderPayment.order_id.ilike(f"{clean}%"))
                .all()
            )
            if payments:
                total = sum(float(p.payment_value or 0.0) for p in payments)
                methods = ", ".join(sorted({p.payment_type for p in payments}))
                installments = max((p.payment_installments or 1) for p in payments)
                rec["output"] = {
                    "charged_total": round(total, 2),
                    "payment_methods": methods,
                    "installments": installments,
                    "line_count": len(payments),
                }
                text = (
                    f"Order #{clean}: charged ${total:.2f} via {methods}"
                    + (f" in {installments} installments" if installments > 1 else "")
                    + f" across {len(payments)} payment line(s)."
                )
                return (text, [rec])

            # DataCo fallback: order_total on the order row
            try:
                dc = db.query(DataCoOrder).filter(DataCoOrder.order_id == int(clean)).first()
            except ValueError:
                dc = None
            if dc:
                rec["output"] = {
                    "charged_total": round(float(dc.order_total or 0.0), 2),
                    "payment_methods": dc.payment_type or "card",
                    "status": dc.order_status,
                }
                return (
                    f"Order #{dc.order_id}: billed ${float(dc.order_total or 0.0):.2f} "
                    f"via {dc.payment_type or 'card'} (order status {dc.order_status}).",
                    [rec],
                )

            # Order exists but no payment lines seeded
            detail = OrdersTools.lookup_order(clean, db=db)
            if detail:
                rec["output"] = {"charged_total": detail.total, "payment_methods": "on file", "source": "order_total"}
                return (
                    f"Order #{detail.order_id}: order value ${detail.total:.2f}. "
                    f"Itemised payment breakdown is not on file for this order.",
                    [rec],
                )

            rec["output"] = "No billing record found."
            return (f"No billing record found for #{order_id}.", [rec])
        finally:
            if close:
                db.close()

    # ── Refund / returns ─────────────────────────────────────────
    @staticmethod
    def refund_flow(order_id: str, message: str, db: Optional[Session] = None) -> Tuple[str, List[Dict[str, Any]]]:
        records: List[Dict[str, Any]] = []
        policy = OrdersTools.get_return_policy()
        records.append({"tool": "tool_get_return_policy", "name": "get_return_policy", "input": {}, "output": policy})

        if not order_id:
            return (
                "Ask the customer for their Order ID to check return eligibility. Policy: "
                "30-day return window from delivery; item must be in original condition; refunds credited 3-5 business days after receiving.",
                records,
            )

        elig = OrdersTools.check_return_eligibility(order_id, db=db)
        records.append({
            "tool": "tool_check_return_eligibility",
            "name": "check_return_eligibility",
            "input": {"order_id": order_id},
            "output": elig.model_dump(),
        })

        if not elig.is_eligible:
            return (f"Order #{order_id} is INELIGIBLE for return: {elig.message}", records)

        wants_action = any(k in (message or "").lower() for k in ["refund", "return", "send it back", "money back", "rma"])
        if wants_action:
            rma = OrdersTools.initiate_return(order_id, reason="Customer requested return", db=db)
            records.append({
                "tool": "tool_initiate_return",
                "name": "initiate_return",
                "input": {"order_id": order_id, "reason": "Customer requested return"},
                "output": rma.model_dump(),
            })
            return (
                f"Order #{order_id} is ELIGIBLE. RMA {rma.rma_number} created for a "
                f"${rma.refund_amount:.2f} refund. {rma.instructions}",
                records,
            )
        return (f"Order #{order_id} is ELIGIBLE for return ({elig.message}). Confirm with the customer before issuing an RMA.", records)

    # ── General ──────────────────────────────────────────────────
    @staticmethod
    def order_status(order_id: str, db: Optional[Session] = None) -> Tuple[str, List[Dict[str, Any]]]:
        if not order_id:
            return ("Ask the customer for an Order ID to look up status and contents.", [])
        detail = OrdersTools.lookup_order(order_id, db=db)
        rec = {"tool": "tool_lookup_order", "name": "lookup_order", "input": {"order_id": order_id}}
        if not detail:
            rec["output"] = "not found"
            return (f"Order #{order_id} was not found.", [rec])
        rec["output"] = detail.model_dump()
        items = ", ".join(it.product_name for it in detail.items[:3]) or "fulfillment package"
        return (
            f"Order #{detail.order_id}: status {detail.status.upper()}, total ${detail.total:.2f}, "
            f"placed {(detail.purchase_timestamp or '')[:10]}, tracking {detail.tracking_number}. Items: {items}.",
            [rec],
        )

    @staticmethod
    def pipeline_snapshot(db: Optional[Session] = None) -> Tuple[str, List[Dict[str, Any]]]:
        s = OrdersTools.get_analytics_summary("all", db=db)
        rec = {"tool": "tool_get_analytics_summary", "name": "get_analytics_summary", "input": {}, "output": s}
        text = (
            f"Store snapshot: {s.get('total_orders', 0):,} orders, "
            f"{s.get('fulfillment_rate_pct', 0):.1f}% fulfilled, "
            f"{s.get('delay_rate_pct', 0):.1f}% delayed, SLA {s.get('sla_health', 'UNKNOWN')}."
        )
        return (text, [rec])
