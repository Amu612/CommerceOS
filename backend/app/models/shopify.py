"""
Shopify live data source — mirrors the `dataco.py` pattern: its own table
names and ID shapes (Shopify's numeric REST IDs, not Olist's 32-char hex
UUIDs), sharing the same declarative `Base` so it lives alongside Olist/DataCo
without touching either. Populated by `app.services.shopify_service`, never
by the historic replay engine.
"""
from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.models.olist import Base


class ShopifyCustomer(Base):
    __tablename__ = "shopify_customers"

    customer_id = Column(BigInteger, primary_key=True)  # Shopify numeric customer id
    email = Column(String(255), nullable=True, index=True)
    first_name = Column(String(120), nullable=True)
    last_name = Column(String(120), nullable=True)
    city = Column(String(120), nullable=True)
    state = Column(String(120), nullable=True)
    country = Column(String(120), nullable=True)
    orders_count = Column(Integer, nullable=False, default=0)
    total_spent = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, nullable=True)
    source = Column(String(20), nullable=False, default="shopify", index=True)

    orders = relationship("ShopifyOrder", back_populates="customer")


class ShopifyProduct(Base):
    __tablename__ = "shopify_products"

    product_id = Column(BigInteger, primary_key=True)  # Shopify numeric product id
    title = Column(String(255), nullable=True)
    product_type = Column(String(120), nullable=True, index=True)  # closest analog to Olist's category
    vendor = Column(String(120), nullable=True)
    price = Column(Float, nullable=True)
    inventory_quantity = Column(Integer, nullable=True)  # Shopify's own on-hand count — real, not modelled
    created_at = Column(DateTime, nullable=True)
    source = Column(String(20), nullable=False, default="shopify", index=True)

    items = relationship("ShopifyOrderItem", back_populates="product")


class ShopifyOrder(Base):
    __tablename__ = "shopify_orders"

    order_id = Column(BigInteger, primary_key=True)  # Shopify numeric order id
    order_number = Column(String(40), nullable=True, index=True)  # human-facing "#1001" style
    customer_id = Column(BigInteger, ForeignKey("shopify_customers.customer_id"), nullable=True, index=True)
    email = Column(String(255), nullable=True)
    financial_status = Column(String(40), nullable=True, index=True)  # paid | pending | refunded | ...
    fulfillment_status = Column(String(40), nullable=True, index=True)  # fulfilled | partial | unfulfilled | null
    currency = Column(String(10), nullable=True)
    total_price = Column(Float, nullable=False, default=0.0)
    total_tax = Column(Float, nullable=False, default=0.0)
    total_discounts = Column(Float, nullable=False, default=0.0)
    shipping_price = Column(Float, nullable=False, default=0.0)
    shipping_city = Column(String(120), nullable=True)
    shipping_state = Column(String(120), nullable=True)
    shipping_country = Column(String(120), nullable=True)
    created_at = Column(DateTime, nullable=False, index=True)
    updated_at = Column(DateTime, nullable=True)
    processed_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    tracking_number = Column(String(120), nullable=True)
    tracking_company = Column(String(120), nullable=True)
    test = Column(Boolean, nullable=False, default=False)  # Shopify's own "this is a dev-store test order" flag
    source = Column(String(20), nullable=False, default="shopify", index=True)

    customer = relationship("ShopifyCustomer", back_populates="orders")
    items = relationship("ShopifyOrderItem", back_populates="order", cascade="all, delete-orphan")


class ShopifyOrderItem(Base):
    __tablename__ = "shopify_order_items"

    line_item_id = Column(BigInteger, primary_key=True)  # Shopify numeric line-item id
    order_id = Column(BigInteger, ForeignKey("shopify_orders.order_id"), nullable=False, index=True)
    product_id = Column(BigInteger, ForeignKey("shopify_products.product_id"), nullable=True, index=True)
    title = Column(String(255), nullable=True)
    quantity = Column(Integer, nullable=False, default=1)
    price = Column(Float, nullable=False, default=0.0)
    total_discount = Column(Float, nullable=False, default=0.0)

    order = relationship("ShopifyOrder", back_populates="items")
    product = relationship("ShopifyProduct", back_populates="items")
