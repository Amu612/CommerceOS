"""
Apify competitor-price feed — additive to the Pricing agent only. Unrelated
to the historic/live order data-source switch: it works the same regardless
of which one is active, since it's about *external* competitor prices, not
our own orders.

Runs (or reads the last run of) an Apify Actor via the Apify REST API and
upserts its results into `app.models.competitor.CompetitorPrice`. The exact
output shape depends on which price-scraping Actor you point `APIFY_ACTOR_ID`
at — this normalizes a few common field-name variants (Apify's e-commerce
scrapers generally return `title`/`price`/`currency` or close variants) and
skips anything it can't parse rather than guessing.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.database.session import SessionLocal
from app.models.competitor import CompetitorPrice

logger = logging.getLogger(__name__)

_APIFY_BASE = "https://api.apify.com/v2"


def _extract_price(item: dict) -> float | None:
    for key in ("price", "currentPrice", "salePrice", "priceValue"):
        v = item.get(key)
        if isinstance(v, int | float):
            return float(v)
        if isinstance(v, dict) and isinstance(v.get("value"), int | float):
            return float(v["value"])
    return None


def _extract_title(item: dict) -> str | None:
    for key in ("title", "name", "productName"):
        if item.get(key):
            return str(item[key])
    return None


def sync_competitor_prices(category: str | None = None) -> dict[str, Any]:
    """Fetches the latest dataset items from the configured Apify Actor's most
    recent successful run and upserts them as CompetitorPrice rows."""
    from app.core.settings import settings

    if not settings.apify_configured:
        return {"status": "NOT_CONFIGURED", "synced": 0, "reason": "APIFY_TOKEN / APIFY_ACTOR_ID not set."}

    try:
        runs_resp = httpx.get(
            f"{_APIFY_BASE}/acts/{settings.APIFY_ACTOR_ID}/runs/last/dataset/items",
            params={"token": settings.APIFY_TOKEN, "format": "json", "clean": "true"},
            timeout=30.0,
        )
        runs_resp.raise_for_status()
        items = runs_resp.json()
    except httpx.HTTPError as exc:
        logger.warning("apify_sync_failed", extra={"error": str(exc)})
        return {"status": "ERROR", "synced": 0, "reason": str(exc)}

    if not isinstance(items, list):
        return {"status": "ERROR", "synced": 0, "reason": "Unexpected Apify response shape."}

    db = SessionLocal()
    synced = 0
    try:
        for item in items:
            if not isinstance(item, dict):
                continue
            price = _extract_price(item)
            title = _extract_title(item)
            if price is None or not title:
                continue
            db.add(
                CompetitorPrice(
                    category=category or str(item.get("category") or "General"),
                    product_match=title,
                    competitor_name=str(
                        item.get("seller") or item.get("store") or item.get("source") or "Unknown"
                    ),
                    price=price,
                    currency=str(item.get("currency") or "INR"),
                    source_url=item.get("url") or item.get("productUrl"),
                )
            )
            synced += 1
        db.commit()
    finally:
        db.close()

    return {"status": "OK", "synced": synced, "fetched": len(items)}
