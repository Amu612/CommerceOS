"""
Competitor price benchmarks — fed by the Apify competitor-price scrape
(`app.services.apify_service`). Additive to the Pricing agent only; unrelated
to which order data source (historic/live) is active.
"""
from __future__ import annotations

from sqlalchemy import Column, DateTime, Float, Integer, String

from app.models.olist import Base
from app.models.security import _utcnow


class CompetitorPrice(Base):
    __tablename__ = "competitor_prices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String(120), nullable=False, index=True)  # matched against our own category names
    product_match = Column(String(255), nullable=True)  # free-text product title, best-effort match
    competitor_name = Column(String(120), nullable=False, index=True)
    price = Column(Float, nullable=False)
    currency = Column(String(10), nullable=False, default="INR")
    source_url = Column(String(500), nullable=True)
    scraped_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
