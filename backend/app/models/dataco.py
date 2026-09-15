from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.models.olist import Base


class DataCoOrder(Base):
    __tablename__ = "dataco_orders"

    order_id = Column(Integer, primary_key=True)
    customer_id = Column(Integer, nullable=False, index=True)
    customer_segment = Column(String(50), nullable=True)
    customer_city = Column(String(100), nullable=True)
    customer_state = Column(String(50), nullable=True)
    customer_country = Column(String(100), nullable=True)
    market = Column(String(50), nullable=True, index=True)
    order_region = Column(String(50), nullable=True, index=True)
    order_country = Column(String(100), nullable=True)
    order_city = Column(String(100), nullable=True)
    order_date = Column(DateTime, nullable=False, index=True)
    shipping_date = Column(DateTime, nullable=True)
    order_status = Column(String(50), nullable=False, index=True)
    shipping_mode = Column(String(50), nullable=False, index=True)
    delivery_status = Column(String(50), nullable=False)
    late_delivery_risk = Column(Integer, nullable=False, default=0, index=True)
    days_for_shipping_real = Column(Float, nullable=True)
    days_for_shipment_scheduled = Column(Float, nullable=True)
    payment_type = Column(String(50), nullable=True)
    order_total = Column(Float, nullable=False, default=0.0)
    order_profit = Column(Float, nullable=False, default=0.0)
    source = Column(String(20), nullable=False, default="dataco", index=True)

    items = relationship("DataCoOrderItem", back_populates="order", cascade="all, delete-orphan")


class DataCoOrderItem(Base):
    __tablename__ = "dataco_order_items"

    order_item_id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("dataco_orders.order_id"), nullable=False, index=True)
    product_card_id = Column(Integer, nullable=False, index=True)
    product_name = Column(String(255), nullable=True)
    category_id = Column(Integer, nullable=False, index=True)
    category_name = Column(String(100), nullable=True)
    department_id = Column(Integer, nullable=True)
    department_name = Column(String(100), nullable=True)
    product_price = Column(Float, nullable=False, default=0.0)
    order_item_quantity = Column(Integer, nullable=False, default=1)
    sales = Column(Float, nullable=False, default=0.0)
    order_item_discount = Column(Float, nullable=False, default=0.0)
    order_item_discount_rate = Column(Float, nullable=False, default=0.0)
    order_item_total = Column(Float, nullable=False, default=0.0)
    order_item_profit_ratio = Column(Float, nullable=False, default=0.0)
    order_profit_per_order = Column(Float, nullable=False, default=0.0)

    order = relationship("DataCoOrder", back_populates="items")
