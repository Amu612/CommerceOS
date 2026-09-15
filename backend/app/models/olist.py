from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class CategoryTranslation(Base):
    __tablename__ = "product_category_name_translation"

    product_category_name = Column(String(100), primary_key=True)
    product_category_name_english = Column(String(100), nullable=False)


class Geolocation(Base):
    __tablename__ = "geolocation"

    id = Column(Integer, primary_key=True, autoincrement=True)
    geolocation_zip_code_prefix = Column(Integer, nullable=False, index=True)
    geolocation_lat = Column(Float, nullable=False)
    geolocation_lng = Column(Float, nullable=False)
    geolocation_city = Column(String(100), nullable=False)
    geolocation_state = Column(String(10), nullable=False)


class Customer(Base):
    __tablename__ = "customers"

    customer_id = Column(String(32), primary_key=True)
    customer_unique_id = Column(String(32), nullable=False, index=True)
    customer_zip_code_prefix = Column(Integer, nullable=False, index=True)
    customer_city = Column(String(100), nullable=False)
    customer_state = Column(String(10), nullable=False)

    orders = relationship("Order", back_populates="customer")


class Seller(Base):
    __tablename__ = "sellers"

    seller_id = Column(String(32), primary_key=True)
    seller_zip_code_prefix = Column(Integer, nullable=False, index=True)
    seller_city = Column(String(100), nullable=False)
    seller_state = Column(String(10), nullable=False)

    order_items = relationship("OrderItem", back_populates="seller")


class Product(Base):
    __tablename__ = "products"

    product_id = Column(String(32), primary_key=True)
    product_category_name = Column(String(100), nullable=True, index=True)
    product_name_lenght = Column(Integer, nullable=True)
    product_description_lenght = Column(Integer, nullable=True)
    product_photos_qty = Column(Integer, nullable=True)
    product_weight_g = Column(Float, nullable=True)
    product_length_cm = Column(Float, nullable=True)
    product_height_cm = Column(Float, nullable=True)
    product_width_cm = Column(Float, nullable=True)

    order_items = relationship("OrderItem", back_populates="product")
    inventory = relationship("Inventory", back_populates="product", uselist=False)


class Order(Base):
    __tablename__ = "orders"

    order_id = Column(String(32), primary_key=True)
    customer_id = Column(String(32), ForeignKey("customers.customer_id"), nullable=False, index=True)
    order_status = Column(String(50), nullable=False, index=True)
    order_purchase_timestamp = Column(DateTime, nullable=False, index=True)
    order_approved_at = Column(DateTime, nullable=True)
    order_delivered_carrier_date = Column(DateTime, nullable=True)
    order_delivered_customer_date = Column(DateTime, nullable=True)
    order_estimated_delivery_date = Column(DateTime, nullable=False)

    customer = relationship("Customer", back_populates="orders")
    items = relationship("OrderItem", back_populates="order")
    payments = relationship("OrderPayment", back_populates="order")
    reviews = relationship("OrderReview", back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"

    order_id = Column(String(32), ForeignKey("orders.order_id"), primary_key=True)
    order_item_id = Column(Integer, primary_key=True)
    product_id = Column(String(32), ForeignKey("products.product_id"), nullable=False, index=True)
    seller_id = Column(String(32), ForeignKey("sellers.seller_id"), nullable=False, index=True)
    shipping_limit_date = Column(DateTime, nullable=False)
    price = Column(Float, nullable=False)
    freight_value = Column(Float, nullable=False)

    order = relationship("Order", back_populates="items")
    product = relationship("Product", back_populates="order_items")
    seller = relationship("Seller", back_populates="order_items")


class OrderPayment(Base):
    __tablename__ = "order_payments"

    order_id = Column(String(32), ForeignKey("orders.order_id"), primary_key=True)
    payment_sequential = Column(Integer, primary_key=True)
    payment_type = Column(String(50), nullable=False)
    payment_installments = Column(Integer, nullable=False)
    payment_value = Column(Float, nullable=False)

    order = relationship("Order", back_populates="payments")


class OrderReview(Base):
    __tablename__ = "order_reviews"

    review_id = Column(String(32), primary_key=True)
    order_id = Column(String(32), ForeignKey("orders.order_id"), primary_key=True)
    review_score = Column(Integer, nullable=False, index=True)
    review_comment_title = Column(Text, nullable=True)
    review_comment_message = Column(Text, nullable=True)
    review_creation_date = Column(DateTime, nullable=False, index=True)
    review_answer_timestamp = Column(DateTime, nullable=True)

    order = relationship("Order", back_populates="reviews")


class Inventory(Base):
    __tablename__ = "inventory"

    product_id = Column(String(32), ForeignKey("products.product_id"), primary_key=True)
    quantity_on_hand = Column(Integer, nullable=False, default=0)
    quantity_reserved = Column(Integer, nullable=False, default=0)
    quantity_available = Column(Integer, nullable=False, default=0)
    reorder_point = Column(Integer, nullable=True)
    reorder_quantity = Column(Integer, nullable=True)
    last_updated = Column(DateTime(timezone=True), nullable=False)
    data_source = Column(String(50), nullable=False, default="UNKNOWN")
    data_status = Column(String(50), nullable=False, default="NOT_ESTIMABLE")

    product = relationship("Product", back_populates="inventory")
