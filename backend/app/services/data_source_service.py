"""
Active data-source flag: which order/customer/product data every agent reads.

`"historic"` — the seeded Olist + DataCo replay dataset (default, always works).
`"live_shopify"` — a connected Shopify store's live orders (`app.models.shopify`),
                    only selectable once Shopify credentials are configured.

This is deliberately a runtime-mutable singleton (like `state_service`), not
just a settings value read once at startup — the frontend's Data Source
switch flips it live via `POST /api/v1/data-source/select`, and every agent's
`data_layer.py` + the shared `simulated_clock()` consult it on every query.

The two sources are never blended: whichever is active, every agent, every
calculation, and every dashboard reflects *only* that source's data.
"""
from __future__ import annotations

import logging
from typing import Literal

DataSource = Literal["historic", "live_shopify"]

logger = logging.getLogger(__name__)


class DataSourceService:
    def __init__(self) -> None:
        self._active: DataSource = "historic"
        self._initialized = False

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        try:
            from app.core.settings import settings

            self._active = settings.resolve_data_source()  # type: ignore[assignment]
        except Exception:  # noqa: BLE001 - settings must never block startup
            self._active = "historic"

    def get_active_source(self) -> DataSource:
        self._ensure_initialized()
        return self._active

    def set_active_source(self, source: str) -> DataSource:
        from app.core.settings import settings

        if source not in ("historic", "live_shopify"):
            raise ValueError(f"Unknown data source '{source}'.")
        if source == "live_shopify" and not settings.shopify_configured:
            raise ValueError(
                "Shopify isn't configured yet — add SHOPIFY_STORE_DOMAIN plus either "
                "SHOPIFY_CLIENT_ID/SHOPIFY_CLIENT_SECRET or SHOPIFY_ADMIN_TOKEN first."
            )
        self._initialized = True
        self._active = source  # type: ignore[assignment]
        logger.info("data_source_changed", extra={"active_source": source})
        return self._active

    def available_sources(self) -> list[DataSource]:
        from app.core.settings import settings

        sources: list[DataSource] = ["historic"]
        if settings.shopify_configured:
            sources.append("live_shopify")
        return sources

    def is_historic(self) -> bool:
        return self.get_active_source() == "historic"

    def is_live(self) -> bool:
        return self.get_active_source() == "live_shopify"


data_source_service = DataSourceService()
