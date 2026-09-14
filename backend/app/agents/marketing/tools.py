"""Marketing LangChain tools — real RFM/demand computation, empirical severities."""
from __future__ import annotations

from langchain_core.tools import tool

from app.agents._shared import fmt_evidence, money
from app.agents.marketing.data_layer import MarketingData
from app.database.session import SessionLocal

CAMPAIGN_PLAYS = {
    "Champions": "Give them VIP perks, early access, and thank-you rewards to keep them happy.",
    "Loyal": "Recommend similar products and offer points for shopping again.",
    "Promising": "Give a small coupon on their next order before they forget about us.",
    "At Risk": "Send a friendly 'we miss you' email with top-selling items and a special discount.",
    "Hibernating": "Send a simple reminder email; do not waste paid ad money on them.",
    "New": "Welcome them with a thank-you note and an easy discount on their second order.",
}


def _db():
    return SessionLocal()


@tool
def rfm_segments() -> dict:
    """RFM customer segmentation: customer counts + share per segment (Champions / Loyal / Promising / At Risk / Hibernating / New), plus repeat rate and AOV. Use for 'who are my best customers', 'break down my segments'."""
    db = _db()
    try:
        r = MarketingData.rfm(db)
        if r.get("status") == "OK":
            for s in r["segments"]:
                s["play"] = CAMPAIGN_PLAYS.get(s["segment"], "targeted offer")
        return r
    finally:
        db.close()


@tool
def category_demand() -> dict:
    """Top categories by units and revenue — the demand mix. Use for 'what is selling', 'demand trend'."""
    db = _db()
    try:
        return MarketingData.category_demand_trend(db)
    finally:
        db.close()


@tool
def customer_segment_mix() -> dict:
    """Customer segment mix breakdown for charts."""
    db = _db()
    try:
        r = MarketingData.rfm(db)
        return {"segments": r.get("segments", [])}
    finally:
        db.close()


@tool
def detect_low_retention() -> dict:
    """Flags a low repeat-purchase rate against a 20% reference."""
    db = _db()
    try:
        r = MarketingData.rfm(db)
        if r.get("status") != "OK" or r["repeat_rate_pct"] >= 20:
            return {"finding": None}
        return {"finding": {
            "category": "RETENTION",
            "severity": "HIGH" if r["repeat_rate_pct"] < 10 else "MEDIUM",
            "title": "Very few buyers are coming back to purchase again",
            "what_happened": f"Only {r['repeat_rate_pct']}% of {r['total_customers']:,} buyers have placed a second order (average order is {money(r['avg_order_value'])}).",
            "why_it_matters": "Finding brand new customers costs a lot more money than keeping the ones we already have. When buyers do not return, sales growth slows down.",
            "recommended_action": "Send a special discount offer to new buyers shortly after their first order to encourage them to buy again.",
            "evidence": fmt_evidence({k: r[k] for k in ("repeat_customers", "repeat_rate_pct", "total_customers")}),
            "confidence": 0.8, "data_status": "CALCULATED", "sample_count": r["total_customers"],
        }}
    finally:
        db.close()


@tool
def detect_churn_risk() -> dict:
    """Flags when 'At Risk' + 'Hibernating' together are >= 25% of the customer base."""
    db = _db()
    try:
        r = MarketingData.rfm(db)
        if r.get("status") != "OK":
            return {"finding": None}
        by = {s["segment"]: s for s in r["segments"]}
        lapsed = by.get("At Risk", {}).get("share_pct", 0) + by.get("Hibernating", {}).get("share_pct", 0)
        if lapsed < 25:
            return {"finding": None}
        return {"finding": {
            "category": "CHURN_RISK",
            "severity": "HIGH" if lapsed >= 45 else "MEDIUM",
            "title": "A large number of past buyers are stopping their purchases",
            "what_happened": f"Past buyers in 'At Risk' ({by.get('At Risk', {}).get('share_pct', 0)}%) and 'Hibernating' ({by.get('Hibernating', {}).get('share_pct', 0)}%) make up {round(lapsed, 1)}% of all customers.",
            "why_it_matters": "These people have bought from us before. Bringing them back costs much less than advertising to strangers.",
            "recommended_action": "Send a friendly 'we miss you' message with our best products and a small discount. Stop paying for internet ads to people who have stopped buying.",
            "evidence": fmt_evidence({"at_risk": by.get("At Risk"), "hibernating": by.get("Hibernating")}),
            "confidence": 0.78, "data_status": "CALCULATED", "sample_count": r["total_customers"],
        }}
    finally:
        db.close()


@tool
def detect_demand_concentration() -> dict:
    """Flags when the top category is >= 25% of units across the top-10 categories."""
    db = _db()
    try:
        cats = MarketingData.category_demand_trend(db)["categories"]
        if not cats:
            return {"finding": None}
        top = cats[0]
        share = round(top["units"] / max(1, sum(c["units"] for c in cats)) * 100, 1)
        if share < 25:
            return {"finding": None}
        return {"finding": {
            "category": "DEMAND_CONCENTRATION",
            "severity": "MEDIUM",
            "title": f"Most sales depend heavily on '{top['category']}'",
            "what_happened": f"'{top['category']}' accounts for {share}% of all items sold among the top {len(cats)} categories ({top['units']:,} items sold, bringing in {money(top['revenue'])}).",
            "why_it_matters": "It is great that this item sells well, but relying too much on one item is risky if supplies run low or interest drops.",
            "recommended_action": f"Put '{top['category']}' in front of new buyers, and offer bundle deals with other popular items so customers buy more variety.",
            "evidence": fmt_evidence(cats[:4]), "confidence": 0.75, "data_status": "CALCULATED",
        }}
    finally:
        db.close()


METRIC_TOOLS = [rfm_segments, category_demand, customer_segment_mix]
DETECTOR_TOOLS = [detect_low_retention, detect_churn_risk, detect_demand_concentration]
