"""
Universal Entity Resolution & Cross-Table Inspection Service.
Resolves ambiguous or cross-table IDs (customer_id, customer_unique_id, seller_id,
product_id, review_id, order_id) across both Olist and DataCo schemas.
Provides rich, relational context for agent LLMs and deterministic tools.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.models.dataco import DataCoOrder, DataCoOrderItem
from app.models.olist import (
    CategoryTranslation,
    Customer,
    Inventory,
    Order,
    OrderItem,
    OrderPayment,
    OrderReview,
    Product,
    Seller,
)

logger = logging.getLogger(__name__)


def clean_identifier(val: str) -> str:
    """Strips common user and UI prefixes/formatting from an ID."""
    if not val:
        return ""
    s = str(val).strip()
    for prefix in (
        "#",
        "ORD-",
        "ord-",
        "CUST-",
        "cust-",
        "PROD-",
        "prod-",
        "SELLER-",
        "seller-",
        "REV-",
        "rev-",
        "TRK-",
        "BR-",
        "DC-",
    ):
        if s.startswith(prefix):
            s = s[len(prefix) :]
    return s.strip()


class EntityResolver:
    """
    Stateless cross-table entity resolver.
    Disambiguates and queries any ID across Olist and DataCo datasets.
    """

    @staticmethod
    def resolve_entity(entity_id: str, db: Session | None = None) -> dict[str, Any]:
        """
        Inspects all major tables to determine the entity type and returns
        relational details and a formatted markdown summary.
        """
        should_close = False
        if db is None:
            db = SessionLocal()
            should_close = True

        try:
            clean_id = clean_identifier(entity_id)
            if not clean_id:
                return {
                    "status": "EMPTY",
                    "entity_type": "unknown",
                    "summary": "No ID provided.",
                }

            is_hex32 = len(clean_id) == 32 and all(c in "0123456789abcdefABCDEF" for c in clean_id)
            is_int = clean_id.isdigit()

            # ── 1. CHECK ORDERS TABLE ─────────────────────────────────
            order_match = None
            if is_hex32 or len(clean_id) >= 6:
                order_match = (
                    db.query(Order)
                    .filter(or_(Order.order_id == clean_id, Order.order_id.ilike(f"{clean_id}%")))
                    .first()
                )
            if not order_match and is_int:
                try:
                    dc_order = db.query(DataCoOrder).filter(DataCoOrder.order_id == int(clean_id)).first()
                    if dc_order:
                        return EntityResolver._format_dataco_order(dc_order, db)
                except Exception:
                    pass

            if order_match:
                return EntityResolver._format_olist_order(order_match, db)

            # ── 2. CHECK CUSTOMERS TABLE ──────────────────────────────
            # Check by customer_id or customer_unique_id
            cust_match = None
            if is_hex32 or len(clean_id) >= 6:
                cust_match = (
                    db.query(Customer)
                    .filter(
                        or_(
                            Customer.customer_id == clean_id,
                            Customer.customer_unique_id == clean_id,
                            Customer.customer_id.ilike(f"{clean_id}%"),
                            Customer.customer_unique_id.ilike(f"{clean_id}%"),
                        )
                    )
                    .first()
                )
            if not cust_match and is_int:
                try:
                    dc_cust_orders = (
                        db.query(DataCoOrder).filter(DataCoOrder.customer_id == int(clean_id)).limit(10).all()
                    )
                    if dc_cust_orders:
                        return EntityResolver._format_dataco_customer(int(clean_id), dc_cust_orders)
                except Exception:
                    pass

            if cust_match:
                return EntityResolver._format_olist_customer(cust_match, db)

            # ── 3. CHECK PRODUCTS TABLE ───────────────────────────────
            prod_match = None
            if is_hex32 or len(clean_id) >= 6:
                prod_match = (
                    db.query(Product)
                    .filter(or_(Product.product_id == clean_id, Product.product_id.ilike(f"{clean_id}%")))
                    .first()
                )
            if not prod_match and is_int:
                try:
                    dc_prod_items = (
                        db.query(DataCoOrderItem)
                        .filter(DataCoOrderItem.product_card_id == int(clean_id))
                        .limit(10)
                        .all()
                    )
                    if dc_prod_items:
                        return EntityResolver._format_dataco_product(int(clean_id), dc_prod_items)
                except Exception:
                    pass

            if prod_match:
                return EntityResolver._format_olist_product(prod_match, db)

            # ── 4. CHECK SELLERS TABLE ────────────────────────────────
            seller_match = None
            if is_hex32 or len(clean_id) >= 6:
                seller_match = (
                    db.query(Seller)
                    .filter(or_(Seller.seller_id == clean_id, Seller.seller_id.ilike(f"{clean_id}%")))
                    .first()
                )
            if seller_match:
                return EntityResolver._format_olist_seller(seller_match, db)

            # ── 5. CHECK REVIEWS TABLE ────────────────────────────────
            review_match = None
            if is_hex32 or len(clean_id) >= 6:
                review_match = (
                    db.query(OrderReview)
                    .filter(
                        or_(OrderReview.review_id == clean_id, OrderReview.review_id.ilike(f"{clean_id}%"))
                    )
                    .first()
                )
            if review_match:
                return EntityResolver._format_olist_review(review_match, db)

            return {
                "status": "NOT_FOUND",
                "entity_type": "unknown",
                "query_id": entity_id,
                "summary": f"No record matching ID '{entity_id}' was found across orders, customers, products, sellers, or reviews.",
            }
        finally:
            if should_close:
                db.close()

    # ── Formatter Helpers ─────────────────────────────────────────────

    @staticmethod
    def _format_olist_order(o: Order, db: Session) -> dict[str, Any]:
        cust = (
            db.query(Customer).filter(Customer.customer_id == o.customer_id).first()
            if o.customer_id
            else None
        )
        items = db.query(OrderItem).filter(OrderItem.order_id == o.order_id).all()
        payments = db.query(OrderPayment).filter(OrderPayment.order_id == o.order_id).all()
        review = db.query(OrderReview).filter(OrderReview.order_id == o.order_id).first()

        item_list = []
        sellers = set()
        for it in items:
            p = db.query(Product).filter(Product.product_id == it.product_id).first()
            cat = p.product_category_name if p else "general"
            item_list.append(
                {
                    "product_id": it.product_id,
                    "category": cat,
                    "price": float(it.price or 0.0),
                    "freight": float(it.freight_value or 0.0),
                    "seller_id": it.seller_id,
                }
            )
            if it.seller_id:
                sellers.add(it.seller_id)

        total_value = (
            sum(float(p.payment_value or 0.0) for p in payments)
            if payments
            else sum(i["price"] + i["freight"] for i in item_list)
        )

        items_desc = (
            "\n".join(
                f"  • Product `{i['product_id'][:8]}...` ({i['category']}) — R${i['price']:.2f} (Seller: `{i['seller_id'][:8]}...`)"
                for i in item_list
            )
            or "  • Items pending itemization"
        )

        review_desc = (
            f"⭐ Review Score: **{review.review_score}/5**" if review else "No review submitted yet."
        )
        if review and (review.review_comment_title or review.review_comment_message):
            review_desc += f"\n  💬 Comment: \"{review.review_comment_title or ''} {review.review_comment_message or ''}\"".strip()

        summary = (
            f"📦 **Order #{o.order_id}**\n"
            f"📌 Status: **{o.order_status.upper()}**\n"
            f"👤 Customer ID: `{o.customer_id}` (Unique ID: `{cust.customer_unique_id if cust else 'N/A'}`)\n"
            f"📍 Destination: {cust.customer_city if cust else 'Unknown'}, {cust.customer_state if cust else 'ST'}\n"
            f"💰 Total Value: **R${total_value:.2f}**\n"
            f"📅 Placed: {o.order_purchase_timestamp.strftime('%Y-%m-%d') if o.order_purchase_timestamp else 'N/A'}\n"
            f"🚚 Delivered: {o.order_delivered_customer_date.strftime('%Y-%m-%d') if o.order_delivered_customer_date else 'Pending'}\n"
            f"{review_desc}\n"
            f"🛒 Items ({len(item_list)}):\n{items_desc}"
        )

        return {
            "status": "FOUND",
            "entity_type": "order",
            "source": "OLIST",
            "entity_id": o.order_id,
            "customer_id": o.customer_id,
            "customer_unique_id": cust.customer_unique_id if cust else None,
            "order_status": o.order_status,
            "total_value": round(total_value, 2),
            "purchase_date": o.order_purchase_timestamp.isoformat() if o.order_purchase_timestamp else None,
            "items_count": len(item_list),
            "items": item_list,
            "seller_ids": list(sellers),
            "review_score": review.review_score if review else None,
            "summary": summary,
        }

    @staticmethod
    def _format_dataco_order(dc: DataCoOrder, db: Session) -> dict[str, Any]:
        items = db.query(DataCoOrderItem).filter(DataCoOrderItem.order_id == dc.order_id).all()
        item_list = [
            {
                "product_id": str(it.product_card_id),
                "product_name": it.product_name,
                "category": it.category_name,
                "quantity": it.order_item_quantity,
                "price": float(it.product_price or 0.0),
                "total": float(it.order_item_total or 0.0),
            }
            for it in items
        ]
        items_desc = (
            "\n".join(
                f"  • {i['product_name']} x{i['quantity']} — ${i['total']:.2f} ({i['category']})"
                for i in item_list
            )
            or "  • Details pending"
        )

        summary = (
            f"📦 **DataCo Order #{dc.order_id}**\n"
            f"📌 Status: **{dc.order_status.upper()}**\n"
            f"👤 Customer ID: `{dc.customer_id}` ({dc.customer_segment or 'Consumer'})\n"
            f"📍 Destination: {dc.customer_city or 'City'}, {dc.customer_state or 'ST'}, {dc.customer_country or 'Country'}\n"
            f"💰 Total Value: **${float(dc.order_total or 0.0):.2f}**\n"
            f"🚚 Delivery Mode: {dc.shipping_mode or 'Standard'} | Status: {dc.delivery_status or 'On Time'}\n"
            f"📅 Placed: {dc.order_date.strftime('%Y-%m-%d') if dc.order_date else 'N/A'}\n"
            f"🛒 Items ({len(item_list)}):\n{items_desc}"
        )

        return {
            "status": "FOUND",
            "entity_type": "order",
            "source": "DATACO",
            "entity_id": str(dc.order_id),
            "customer_id": str(dc.customer_id),
            "order_status": dc.order_status,
            "total_value": float(dc.order_total or 0.0),
            "purchase_date": dc.order_date.isoformat() if dc.order_date else None,
            "items_count": len(item_list),
            "items": item_list,
            "summary": summary,
        }

    @staticmethod
    def _format_olist_customer(cust: Customer, db: Session) -> dict[str, Any]:
        linked_cust_ids = [
            c.customer_id
            for c in db.query(Customer.customer_id)
            .filter(Customer.customer_unique_id == cust.customer_unique_id)
            .all()
        ]
        if not linked_cust_ids:
            linked_cust_ids = [cust.customer_id]

        orders = (
            db.query(Order)
            .filter(Order.customer_id.in_(linked_cust_ids))
            .order_by(Order.order_purchase_timestamp.desc())
            .all()
        )

        order_summaries = []
        total_spent = 0.0
        for o in orders:
            pmts = db.query(OrderPayment).filter(OrderPayment.order_id == o.order_id).all()
            o_val = sum(float(p.payment_value or 0.0) for p in pmts) if pmts else 0.0
            total_spent += o_val
            order_summaries.append(
                {
                    "order_id": o.order_id,
                    "status": o.order_status,
                    "date": (
                        o.order_purchase_timestamp.strftime("%Y-%m-%d")
                        if o.order_purchase_timestamp
                        else "N/A"
                    ),
                    "value": round(o_val, 2),
                }
            )

        orders_desc = (
            "\n".join(
                f"  • Order `#{o['order_id'][:8]}...` — **{o['status'].upper()}** on {o['date']} (R${o['value']:.2f})"
                for o in order_summaries[:5]
            )
            if order_summaries
            else "  • No orders placed yet."
        )

        summary = (
            f"👤 **Customer Profile**\n"
            f"🔑 Customer ID: `{cust.customer_id}`\n"
            f"🆔 Unique Customer ID: `{cust.customer_unique_id}`\n"
            f"📍 Location: {cust.customer_city.title()}, {cust.customer_state} (ZIP prefix: {cust.customer_zip_code_prefix})\n"
            f"📊 Total Orders: **{len(orders)}** | Lifetime Spend: **R${total_spent:.2f}**\n"
            f"📦 Associated Orders:\n{orders_desc}"
        )

        return {
            "status": "FOUND",
            "entity_type": "customer",
            "source": "OLIST",
            "entity_id": cust.customer_id,
            "customer_id": cust.customer_id,
            "customer_unique_id": cust.customer_unique_id,
            "city": cust.customer_city,
            "state": cust.customer_state,
            "zip_code": cust.customer_zip_code_prefix,
            "orders_count": len(orders),
            "total_spent": round(total_spent, 2),
            "orders": order_summaries,
            "summary": summary,
        }

    @staticmethod
    def _format_dataco_customer(cust_id: int, orders: list[DataCoOrder]) -> dict[str, Any]:
        first = orders[0]
        total_spent = sum(float(o.order_total or 0.0) for o in orders)
        order_summaries = [
            {
                "order_id": str(o.order_id),
                "status": o.order_status,
                "date": o.order_date.strftime("%Y-%m-%d") if o.order_date else "N/A",
                "value": round(float(o.order_total or 0.0), 2),
            }
            for o in orders
        ]
        orders_desc = "\n".join(
            f"  • Order `#{o['order_id']}` — **{o['status'].upper()}** on {o['date']} (${o['value']:.2f})"
            for o in order_summaries[:5]
        )

        summary = (
            f"👤 **DataCo Customer #{cust_id}**\n"
            f"🏷 Segment: {first.customer_segment or 'Consumer'}\n"
            f"📍 Location: {first.customer_city or 'City'}, {first.customer_state or 'ST'}, {first.customer_country or 'Country'}\n"
            f"📊 Total Orders: **{len(orders)}** | Lifetime Spend: **${total_spent:.2f}**\n"
            f"📦 Associated Orders:\n{orders_desc}"
        )

        return {
            "status": "FOUND",
            "entity_type": "customer",
            "source": "DATACO",
            "entity_id": str(cust_id),
            "customer_id": str(cust_id),
            "segment": first.customer_segment,
            "city": first.customer_city,
            "state": first.customer_state,
            "country": first.customer_country,
            "orders_count": len(orders),
            "total_spent": round(total_spent, 2),
            "orders": order_summaries,
            "summary": summary,
        }

    @staticmethod
    def _format_olist_product(p: Product, db: Session) -> dict[str, Any]:
        cat_en = None
        if p.product_category_name:
            trans = (
                db.query(CategoryTranslation)
                .filter(CategoryTranslation.product_category_name == p.product_category_name)
                .first()
            )
            if trans:
                cat_en = trans.product_category_name_english

        items = db.query(OrderItem).filter(OrderItem.product_id == p.product_id).limit(20).all()
        prices = [float(it.price or 0.0) for it in items]
        avg_price = round(sum(prices) / len(prices), 2) if prices else 0.0
        orders_set = list({it.order_id for it in items})

        inv = db.query(Inventory).filter(Inventory.product_id == p.product_id).first()
        stock = inv.quantity_available if inv else "Unknown (Catalog item)"

        summary = (
            f"🛍 **Product #{p.product_id}**\n"
            f"📂 Category: **{(cat_en or p.product_category_name or 'General').title()}**\n"
            f"💵 Average Price: **R${avg_price:.2f}** | Units Sold: **{len(items)}**\n"
            f"📦 Inventory On Hand: **{stock}**\n"
            f"📐 Dimensions: {p.product_length_cm or 0}x{p.product_width_cm or 0}x{p.product_height_cm or 0} cm | Weight: {p.product_weight_g or 0} g\n"
            f"📋 Sample Associated Orders: {', '.join(f'`#{oid[:8]}...`' for oid in orders_set[:5]) if orders_set else 'None'}"
        )

        return {
            "status": "FOUND",
            "entity_type": "product",
            "source": "OLIST",
            "entity_id": p.product_id,
            "product_id": p.product_id,
            "category": p.product_category_name,
            "category_english": cat_en,
            "avg_price": avg_price,
            "units_sold": len(items),
            "stock": stock,
            "sample_orders": orders_set[:5],
            "summary": summary,
        }

    @staticmethod
    def _format_dataco_product(card_id: int, items: list[DataCoOrderItem]) -> dict[str, Any]:
        first = items[0]
        prices = [float(it.product_price or 0.0) for it in items]
        avg_price = round(sum(prices) / len(prices), 2) if prices else 0.0
        orders_set = list({str(it.order_id) for it in items})

        summary = (
            f"🛍 **DataCo Product #{card_id}**\n"
            f"🏷 Name: **{first.product_name or 'Product'}**\n"
            f"📂 Category: **{first.category_name or 'General'}** (Dept: {first.department_name or 'General'})\n"
            f"💵 Price: **${avg_price:.2f}** | Total Lines: **{len(items)}**\n"
            f"📋 Sample Associated Orders: {', '.join(f'`#{oid}`' for oid in orders_set[:5])}"
        )

        return {
            "status": "FOUND",
            "entity_type": "product",
            "source": "DATACO",
            "entity_id": str(card_id),
            "product_id": str(card_id),
            "name": first.product_name,
            "category": first.category_name,
            "price": avg_price,
            "sample_orders": orders_set[:5],
            "summary": summary,
        }

    @staticmethod
    def _format_olist_seller(seller: Seller, db: Session) -> dict[str, Any]:
        items = db.query(OrderItem).filter(OrderItem.seller_id == seller.seller_id).limit(50).all()
        unique_prods = list({it.product_id for it in items})
        unique_orders = list({it.order_id for it in items})
        revenue = sum(float(it.price or 0.0) for it in items)

        summary = (
            f"🏪 **Seller #{seller.seller_id}**\n"
            f"📍 Location: {seller.seller_city.title()}, {seller.seller_state} (ZIP prefix: {seller.seller_zip_code_prefix})\n"
            f"📊 Order Items Fulfilled: **{len(items)}** across **{len(unique_orders)}** orders\n"
            f"💰 Approximate Revenue: **R${revenue:.2f}**\n"
            f"🛍 Unique Products Sold: **{len(unique_prods)}**\n"
            f"📋 Sample Product IDs: {', '.join(f'`{pid[:8]}...`' for pid in unique_prods[:5]) if unique_prods else 'None'}"
        )

        return {
            "status": "FOUND",
            "entity_type": "seller",
            "source": "OLIST",
            "entity_id": seller.seller_id,
            "seller_id": seller.seller_id,
            "city": seller.seller_city,
            "state": seller.seller_state,
            "items_count": len(items),
            "orders_count": len(unique_orders),
            "unique_products_count": len(unique_prods),
            "revenue": round(revenue, 2),
            "summary": summary,
        }

    @staticmethod
    def _format_olist_review(review: OrderReview, db: Session) -> dict[str, Any]:
        order = db.query(Order).filter(Order.order_id == review.order_id).first()

        summary = (
            f"⭐ **Order Review #{review.review_id}**\n"
            f"📌 Rating: **{review.review_score} / 5 stars**\n"
            f"📦 Linked Order: `#{review.order_id}` (Status: {order.order_status.upper() if order else 'N/A'})\n"
            f"📅 Submitted: {review.review_creation_date.strftime('%Y-%m-%d') if review.review_creation_date else 'N/A'}\n"
            f"📝 Title: {review.review_comment_title or '*(No title)*'}\n"
            f"💬 Message: {review.review_comment_message or '*(No comment text)*'}"
        )

        return {
            "status": "FOUND",
            "entity_type": "review",
            "source": "OLIST",
            "entity_id": review.review_id,
            "review_id": review.review_id,
            "order_id": review.order_id,
            "score": review.review_score,
            "title": review.review_comment_title,
            "message": review.review_comment_message,
            "summary": summary,
        }


# Canonical singleton
entity_resolver = EntityResolver()
