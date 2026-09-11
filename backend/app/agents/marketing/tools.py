"""Marketing LangChain tools — real RFM/demand computation, empirical severities."""
from __future__ import annotations

from langchain_core.tools import tool

from app.agents._shared import money
from app.agents.marketing.data_layer import MarketingData
from app.database.session import SessionLocal

CAMPAIGN_PLAYS = {
    "Champions": "VIP early-access + referral incentive — protect and amplify.",
    "Loyal": "Cross-sell adjacent categories + loyalty tier nudge.",
    "Promising": "Second-purchase offer within the recency window (time-boxed).",
    "At Risk": "Win-back sequence: reminder + modest incentive + best-seller picks.",
    "Hibernating": "Low-cost reactivation email; suppress paid spend.",
    "New": "Onboarding series + first-repeat incentive.",
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
    """DataCo customer-segment mix (Consumer / Corporate / Home Office) by order count, share, and revenue."""
    db = _db()
    try:
        return MarketingData.segment_mix(db)
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
            "title": "Repeat-purchase rate is low",
            "what_happened": f"Only {r['repeat_rate_pct']}% of {r['total_customers']:,} customers have made a second purchase (AOV {money(r['avg_order_value'])}).",
            "why_it_matters": "Acquisition is far more expensive than retention; a low repeat rate caps LTV and CAC payback.",
            "recommended_action": "Launch a second-purchase program for 'Promising' + 'New' segments with a time-boxed incentive inside the observed inter-purchase window.",
            "evidence": str({k: r[k] for k in ("repeat_customers", "repeat_rate_pct", "total_customers")}),
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
            "title": "A large share of the base is lapsing",
            "what_happened": f"'At Risk' ({by.get('At Risk', {}).get('share_pct', 0)}%) + 'Hibernating' ({by.get('Hibernating', {}).get('share_pct', 0)}%) = {round(lapsed, 1)}% of customers.",
            "why_it_matters": "These customers already converted once; reactivation is cheaper than net-new acquisition.",
            "recommended_action": "Run a staged win-back (reminder → best-sellers → modest incentive). Suppress paid retargeting for 'Hibernating' to protect ROAS.",
            "evidence": str({"at_risk": by.get("At Risk"), "hibernating": by.get("Hibernating")}),
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
            "title": f"Demand concentrated in '{top['category']}'",
            "what_happened": f"'{top['category']}' is {share}% of units in the top-{len(cats)} categories ({top['units']:,} units, {money(top['revenue'])}).",
            "why_it_matters": "Concentration is a growth lever (double down) and a risk (exposure to that category's supply/seasonality).",
            "recommended_action": f"Feature '{top['category']}' in acquisition creative; test cross-sell bundles into the #2–#4 categories to broaden the basket.",
            "evidence": str(cats[:4]), "confidence": 0.75, "data_status": "CALCULATED",
        }}
    finally:
        db.close()


METRIC_TOOLS = [rfm_segments, category_demand, customer_segment_mix]
DETECTOR_TOOLS = [detect_low_retention, detect_churn_risk, detect_demand_concentration]
