"""
Shopify live data source: manual sync (works everywhere, no public URL
needed) + webhook receiver (instant push, once the app is reachable over
HTTPS). Both funnel through `shopify_service._ingest_shopify_record`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request

from app.database.session import SessionLocal
from app.services.shopify_service import _ingest_shopify_record, pull_latest_orders

logger = logging.getLogger(__name__)

# Two routers, not one: `/sync` is called from our own authenticated frontend
# (same soft/strict auth as every other agent route); `/webhook` is called by
# Shopify itself and can never carry our JWT — its security is the HMAC
# signature check below, so it must stay outside the auth-gated router group.
router = APIRouter(prefix="/api/v1/shopify", tags=["Shopify"])
webhook_router = APIRouter(prefix="/api/v1/shopify", tags=["Shopify"])


def _verify_webhook(raw_body: bytes, hmac_header: str | None) -> bool:
    from app.core.settings import settings

    if not settings.SHOPIFY_WEBHOOK_SECRET or not hmac_header:
        return False
    digest = hmac.new(settings.SHOPIFY_WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, hmac_header)


@router.post("/sync")
def sync_now(limit: int = 50):
    """Manual 'Sync now' — pulls the most recently updated Shopify orders. No
    public URL required; this is the reliable path for local dev / a demo."""
    return pull_latest_orders(limit=limit)


@webhook_router.post("/webhook")
async def webhook(request: Request, x_shopify_hmac_sha256: str | None = Header(default=None)):
    """Shopify push endpoint for orders/create, orders/updated, orders/fulfilled.
    Configure this URL (once the app has a public HTTPS address) in the
    Shopify custom app's webhook settings, using SHOPIFY_WEBHOOK_SECRET."""
    raw_body = await request.body()
    if not _verify_webhook(raw_body, x_shopify_hmac_sha256):
        raise HTTPException(status_code=401, detail="Invalid Shopify webhook signature.")

    try:
        payload = json.loads(raw_body)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid JSON payload.")

    topic = request.headers.get("X-Shopify-Topic", "")
    if not topic.startswith("orders/"):
        # inventory_levels/*, customers/* etc. — not order data, nothing to ingest yet.
        return {"status": "ignored", "topic": topic}

    db = SessionLocal()
    try:
        order = _ingest_shopify_record(payload, db)
    finally:
        db.close()
    return {"status": "OK", "order_id": order.order_id, "topic": topic}
