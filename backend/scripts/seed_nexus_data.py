"""
Nexus Dataset Ingestion Script.
Reads real Olist and DataCo transaction records from the Nexus (CommerceOS) dataset directory
and populates the Orders Agent SQLite database.
"""
import os
import sys
import logging
from datetime import datetime, timezone
from typing import List
import pandas as pd
from sqlalchemy.orm import Session

# Add backend directory to sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.database.session import SessionLocal, init_db
from app.models.olist import Customer, Order, OrderItem, Product
from app.models.dataco import DataCoOrder, DataCoOrderItem

logger = logging.getLogger("seed_nexus_data")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Dataset directory: settings.DATASET_DIR / NEXUS_DATA_DIR env, else project-relative fallbacks.
def _get_nexus_data_dirs() -> List[str]:
    for candidate in (os.environ.get("DATASET_DIR"), os.environ.get("NEXUS_DATA_DIR")):
        if candidate and os.path.isdir(candidate):
            return [candidate]
    try:
        from app.core.settings import settings

        if settings.DATASET_DIR and os.path.isdir(settings.DATASET_DIR):
            return [settings.DATASET_DIR]
    except Exception:
        pass
    # Project-relative fallbacks
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_dir = os.path.dirname(backend_dir)
    parent_dir = os.path.dirname(project_dir)
    dirs = [
        os.path.join(backend_dir, "data", "raw"),
        os.path.join(backend_dir, "data", "processed"),
        os.path.join(project_dir, "data", "raw"),
        os.path.join(project_dir, "data", "processed"),
        os.path.join(parent_dir, "updated_CommerceOS", "backend", "app", "data", "raw"),
        os.path.join(parent_dir, "updated_CommerceOS", "backend", "app", "data", "processed"),
        os.path.join(parent_dir, "archive (1)"),
    ]
    return [d for d in dirs if os.path.isdir(d)]


def find_dataset(filename: str) -> str:
    for d in _get_nexus_data_dirs():
        p = os.path.join(d, filename)
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"Dataset {filename} not found in Nexus data paths: {_get_nexus_data_dirs()}")


def clean_val(v):
    if pd.isna(v):
        return None
    return v


def seed_data(db: Session = None, olist_limit: int = 25000, dataco_limit: int = 8000):
    should_close = False
    if db is None:
        init_db()
        db = SessionLocal()
        should_close = True

    try:
        logger.info("Starting Nexus dataset ingestion into Orders Agent DB...")

        # 1. Ingest Olist Customers
        cust_path = find_dataset("olist_customers_dataset.csv")
        logger.info(f"Loading customers from {cust_path}...")
        df_cust = pd.read_csv(cust_path, nrows=olist_limit)
        existing_custs = set(r[0] for r in db.query(Customer.customer_id).all())
        cust_records = []
        for _, row in df_cust.iterrows():
            cid = str(row["customer_id"])
            if cid in existing_custs:
                continue
            existing_custs.add(cid)
            cust_records.append({
                "customer_id": cid,
                "customer_unique_id": str(row["customer_unique_id"]),
                "customer_zip_code_prefix": int(row["customer_zip_code_prefix"]),
                "customer_city": str(row["customer_city"]),
                "customer_state": str(row["customer_state"]),
            })
        if cust_records:
            db.bulk_insert_mappings(Customer, cust_records)
            db.commit()
            logger.info(f"Inserted {len(cust_records)} Olist customers.")

        # 2. Ingest Olist Products
        prod_path = find_dataset("olist_products_dataset.csv")
        logger.info(f"Loading products from {prod_path}...")
        df_prod = pd.read_csv(prod_path, nrows=olist_limit)
        existing_prods = set(r[0] for r in db.query(Product.product_id).all())
        prod_records = []
        for _, row in df_prod.iterrows():
            pid = str(row["product_id"])
            if pid in existing_prods:
                continue
            existing_prods.add(pid)
            prod_records.append({
                "product_id": pid,
                "product_category_name": clean_val(row.get("product_category_name")),
                "product_name_lenght": int(row["product_name_lenght"]) if pd.notna(row.get("product_name_lenght")) else None,
                "product_description_lenght": int(row["product_description_lenght"]) if pd.notna(row.get("product_description_lenght")) else None,
                "product_photos_qty": int(row["product_photos_qty"]) if pd.notna(row.get("product_photos_qty")) else None,
                "product_weight_g": float(row["product_weight_g"]) if pd.notna(row.get("product_weight_g")) else None,
                "product_length_cm": float(row["product_length_cm"]) if pd.notna(row.get("product_length_cm")) else None,
                "product_height_cm": float(row["product_height_cm"]) if pd.notna(row.get("product_height_cm")) else None,
                "product_width_cm": float(row["product_width_cm"]) if pd.notna(row.get("product_width_cm")) else None,
            })
        if prod_records:
            db.bulk_insert_mappings(Product, prod_records)
            db.commit()
            logger.info(f"Inserted {len(prod_records)} Olist products.")

        # 3. Ingest Olist Orders
        orders_path = find_dataset("olist_orders_dataset.csv")
        logger.info(f"Loading orders from {orders_path}...")
        df_orders = pd.read_csv(orders_path, nrows=olist_limit)
        for col in [
            "order_purchase_timestamp",
            "order_approved_at",
            "order_delivered_carrier_date",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ]:
            df_orders[col] = pd.to_datetime(df_orders[col], errors="coerce")

        existing_orders = set(r[0] for r in db.query(Order.order_id).all())
        valid_customers = set(r[0] for r in db.query(Customer.customer_id).all())

        order_records = []
        for _, row in df_orders.iterrows():
            oid = str(row["order_id"])
            cid = str(row["customer_id"])
            if oid in existing_orders:
                continue
            if cid not in valid_customers:
                # Add placeholder customer so foreign key satisfies
                valid_customers.add(cid)
                db.add(Customer(
                    customer_id=cid,
                    customer_unique_id=cid,
                    customer_zip_code_prefix=1000,
                    customer_city="Sao Paulo",
                    customer_state="SP",
                ))
                db.commit()

            existing_orders.add(oid)
            order_records.append({
                "order_id": oid,
                "customer_id": cid,
                "order_status": str(row["order_status"]),
                "order_purchase_timestamp": row["order_purchase_timestamp"].to_pydatetime() if pd.notna(row["order_purchase_timestamp"]) else datetime.now(timezone.utc),
                "order_approved_at": row["order_approved_at"].to_pydatetime() if pd.notna(row["order_approved_at"]) else None,
                "order_delivered_carrier_date": row["order_delivered_carrier_date"].to_pydatetime() if pd.notna(row["order_delivered_carrier_date"]) else None,
                "order_delivered_customer_date": row["order_delivered_customer_date"].to_pydatetime() if pd.notna(row["order_delivered_customer_date"]) else None,
                "order_estimated_delivery_date": row["order_estimated_delivery_date"].to_pydatetime() if pd.notna(row["order_estimated_delivery_date"]) else datetime.now(timezone.utc),
            })

        if order_records:
            db.bulk_insert_mappings(Order, order_records)
            db.commit()
            logger.info(f"Inserted {len(order_records)} Olist orders.")

        # 3b. Ingest Olist Sellers (FK target for order_items).
        try:
            from app.models.olist import Seller

            sell_path = find_dataset("olist_sellers_dataset.csv")
            df_sell = pd.read_csv(sell_path)
            existing_sellers = set(r[0] for r in db.query(Seller.seller_id).all())
            sell_records = [
                {
                    "seller_id": str(r["seller_id"]),
                    "seller_zip_code_prefix": int(r["seller_zip_code_prefix"]) if pd.notna(r.get("seller_zip_code_prefix")) else 0,
                    "seller_city": str(r.get("seller_city") or "unknown"),
                    "seller_state": str(r.get("seller_state") or "NA"),
                }
                for _, r in df_sell.iterrows()
                if str(r["seller_id"]) not in existing_sellers
            ]
            if sell_records:
                db.bulk_insert_mappings(Seller, sell_records)
                db.commit()
                logger.info(f"Inserted {len(sell_records)} Olist sellers.")
        except Exception as e:
            logger.warning(f"Seller seed skipped: {e}")

        # 4. Ingest Olist Order Items
        items_path = find_dataset("olist_order_items_dataset.csv")
        logger.info(f"Loading order items from {items_path}...")
        df_items = pd.read_csv(items_path, nrows=olist_limit * 4)
        df_items["shipping_limit_date"] = pd.to_datetime(df_items["shipping_limit_date"], errors="coerce")

        existing_items = set((r[0], r[1]) for r in db.query(OrderItem.order_id, OrderItem.order_item_id).all())
        valid_orders = set(r[0] for r in db.query(Order.order_id).all())

        item_records = []
        for _, row in df_items.iterrows():
            oid = str(row["order_id"])
            item_seq = int(row["order_item_id"])
            if (oid, item_seq) in existing_items or oid not in valid_orders:
                continue
            existing_items.add((oid, item_seq))
            item_records.append({
                "order_id": oid,
                "order_item_id": item_seq,
                "product_id": str(row["product_id"]),
                "seller_id": str(row["seller_id"]),
                "shipping_limit_date": row["shipping_limit_date"].to_pydatetime() if pd.notna(row["shipping_limit_date"]) else datetime.now(timezone.utc),
                "price": float(row["price"]) if pd.notna(row.get("price")) else 0.0,
                "freight_value": float(row["freight_value"]) if pd.notna(row.get("freight_value")) else 0.0,
            })

        if item_records:
            # Ensure every referenced product + seller exists (Postgres enforces FKs).
            from app.models.olist import Seller

            existing_prods = set(r[0] for r in db.query(Product.product_id).all())
            existing_sellers = set(r[0] for r in db.query(Seller.seller_id).all())
            missing_prods = {r["product_id"] for r in item_records} - existing_prods
            missing_sellers = {r["seller_id"] for r in item_records} - existing_sellers
            if missing_prods:
                db.bulk_insert_mappings(Product, [{"product_id": p, "product_category_name": "general"} for p in missing_prods])
            if missing_sellers:
                db.bulk_insert_mappings(Seller, [
                    {"seller_id": s, "seller_zip_code_prefix": 0, "seller_city": "unknown", "seller_state": "NA"}
                    for s in missing_sellers
                ])
            db.commit()
            db.bulk_insert_mappings(OrderItem, item_records)
            db.commit()
            logger.info(f"Inserted {len(item_records)} Olist order items (+{len(missing_prods)} products, +{len(missing_sellers)} sellers).")

        # 4b. Category translation (Portuguese -> English) — powers product search / pricing.
        try:
            from app.models.olist import CategoryTranslation

            ct_path = find_dataset("product_category_name_translation.csv")
            df_ct = pd.read_csv(ct_path)
            existing_ct = set(r[0] for r in db.query(CategoryTranslation.product_category_name).all())
            ct_records = [
                {"product_category_name": str(r["product_category_name"]),
                 "product_category_name_english": str(r["product_category_name_english"])}
                for _, r in df_ct.iterrows()
                if str(r["product_category_name"]) not in existing_ct
            ]
            if ct_records:
                db.bulk_insert_mappings(CategoryTranslation, ct_records)
                db.commit()
                logger.info(f"Inserted {len(ct_records)} category translations.")
        except Exception as e:
            logger.warning(f"Category translation seed skipped: {e}")

        # 4c. Order payments — real billing data (removes hardcoded order-total fallbacks).
        try:
            from app.models.olist import OrderPayment

            pay_path = find_dataset("olist_order_payments_dataset.csv")
            df_pay = pd.read_csv(pay_path, nrows=olist_limit * 3)
            valid_orders = set(r[0] for r in db.query(Order.order_id).all())
            existing_pay = set((r[0], r[1]) for r in db.query(OrderPayment.order_id, OrderPayment.payment_sequential).all())
            pay_records = []
            for _, row in df_pay.iterrows():
                oid = str(row["order_id"])
                seq = int(row["payment_sequential"])
                if oid not in valid_orders or (oid, seq) in existing_pay:
                    continue
                existing_pay.add((oid, seq))
                pay_records.append({
                    "order_id": oid,
                    "payment_sequential": seq,
                    "payment_type": str(row.get("payment_type") or "credit_card"),
                    "payment_installments": int(row["payment_installments"]) if pd.notna(row.get("payment_installments")) else 1,
                    "payment_value": float(row["payment_value"]) if pd.notna(row.get("payment_value")) else 0.0,
                })
            if pay_records:
                db.bulk_insert_mappings(OrderPayment, pay_records)
                db.commit()
                logger.info(f"Inserted {len(pay_records)} order payments.")
        except Exception as e:
            logger.warning(f"Order payment seed skipped: {e}")

        # 4d. Order reviews — real CSAT / sentiment data.
        try:
            from app.models.olist import OrderReview

            rev_path = find_dataset("olist_order_reviews_dataset.csv")
            df_rev = pd.read_csv(rev_path, nrows=olist_limit * 4)
            for col in ("review_creation_date", "review_answer_timestamp"):
                df_rev[col] = pd.to_datetime(df_rev[col], errors="coerce")
            valid_orders = set(r[0] for r in db.query(Order.order_id).all())
            existing_rev = set((r[0], r[1]) for r in db.query(OrderReview.review_id, OrderReview.order_id).all())
            rev_records = []
            for _, row in df_rev.iterrows():
                rid, oid = str(row["review_id"]), str(row["order_id"])
                if oid not in valid_orders or (rid, oid) in existing_rev:
                    continue
                existing_rev.add((rid, oid))
                rev_records.append({
                    "review_id": rid,
                    "order_id": oid,
                    "review_score": int(row["review_score"]) if pd.notna(row.get("review_score")) else 3,
                    "review_comment_title": clean_val(row.get("review_comment_title")),
                    "review_comment_message": clean_val(row.get("review_comment_message")),
                    "review_creation_date": row["review_creation_date"].to_pydatetime() if pd.notna(row["review_creation_date"]) else datetime.now(timezone.utc),
                    "review_answer_timestamp": row["review_answer_timestamp"].to_pydatetime() if pd.notna(row["review_answer_timestamp"]) else None,
                })
            if rev_records:
                db.bulk_insert_mappings(OrderReview, rev_records)
                db.commit()
                logger.info(f"Inserted {len(rev_records)} order reviews.")
        except Exception as e:
            logger.warning(f"Order review seed skipped: {e}")

        # 5. Ingest DataCo Orders
        try:
            dataco_path = find_dataset("DataCoSupplyChainDataset.csv")
            logger.info(f"Loading DataCo supply chain orders from {dataco_path}...")
            usecols = [
                "Order Id",
                "Order Item Id",
                "Order Customer Id",
                "Customer Segment",
                "Customer City",
                "Customer State",
                "Customer Country",
                "Market",
                "Order Region",
                "Order Country",
                "Order City",
                "order date (DateOrders)",
                "shipping date (DateOrders)",
                "Order Status",
                "Shipping Mode",
                "Delivery Status",
                "Late_delivery_risk",
                "Days for shipping (real)",
                "Days for shipment (scheduled)",
                "Type",
                "Order Profit Per Order",
                "Product Card Id",
                "Product Name",
                "Category Id",
                "Category Name",
                "Department Id",
                "Department Name",
                "Product Price",
                "Order Item Quantity",
                "Sales",
                "Order Item Discount",
                "Order Item Discount Rate",
                "Order Item Total",
                "Order Item Profit Ratio",
            ]
            df_dc = pd.read_csv(dataco_path, usecols=usecols, nrows=dataco_limit, encoding="latin1")
            df_dc["order date (DateOrders)"] = pd.to_datetime(df_dc["order date (DateOrders)"], errors="coerce")
            df_dc["shipping date (DateOrders)"] = pd.to_datetime(df_dc["shipping date (DateOrders)"], errors="coerce")

            existing_dc_orders = set(r[0] for r in db.query(DataCoOrder.order_id).all())
            dc_order_map = {}
            dc_item_records = []

            for _, row in df_dc.iterrows():
                oid = int(row["Order Id"])
                if oid not in existing_dc_orders:
                    if oid not in dc_order_map:
                        order_dt = row["order date (DateOrders)"].to_pydatetime() if pd.notna(row["order date (DateOrders)"]) else datetime.now(timezone.utc)
                        ship_dt = row["shipping date (DateOrders)"].to_pydatetime() if pd.notna(row["shipping date (DateOrders)"]) else None
                        dc_order_map[oid] = {
                            "order_id": oid,
                            "customer_id": int(row["Order Customer Id"]) if pd.notna(row.get("Order Customer Id")) else 0,
                            "customer_segment": clean_val(row.get("Customer Segment")),
                            "customer_city": clean_val(row.get("Customer City")),
                            "customer_state": clean_val(row.get("Customer State")),
                            "customer_country": clean_val(row.get("Customer Country")),
                            "market": clean_val(row.get("Market")),
                            "order_region": clean_val(row.get("Order Region")),
                            "order_country": clean_val(row.get("Order Country")),
                            "order_city": clean_val(row.get("Order City")),
                            "order_date": order_dt,
                            "shipping_date": ship_dt,
                            "order_status": str(row.get("Order Status") or "COMPLETE").strip(),
                            "shipping_mode": str(row.get("Shipping Mode") or "Standard Class").strip(),
                            "delivery_status": str(row.get("Delivery Status") or "Standard").strip(),
                            "late_delivery_risk": int(row["Late_delivery_risk"]) if pd.notna(row.get("Late_delivery_risk")) else 0,
                            "days_for_shipping_real": float(row["Days for shipping (real)"]) if pd.notna(row.get("Days for shipping (real)")) else None,
                            "days_for_shipment_scheduled": float(row["Days for shipment (scheduled)"]) if pd.notna(row.get("Days for shipment (scheduled)")) else None,
                            "payment_type": clean_val(row.get("Type")),
                            "order_total": float(row["Order Item Total"]) if pd.notna(row.get("Order Item Total")) else 0.0,
                            "order_profit": float(row["Order Profit Per Order"]) if pd.notna(row.get("Order Profit Per Order")) else 0.0,
                            "source": "dataco",
                        }
                    else:
                        dc_order_map[oid]["order_total"] += float(row["Order Item Total"]) if pd.notna(row.get("Order Item Total")) else 0.0
                        dc_order_map[oid]["order_profit"] += float(row["Order Profit Per Order"]) if pd.notna(row.get("Order Profit Per Order")) else 0.0

            if dc_order_map:
                db.bulk_insert_mappings(DataCoOrder, list(dc_order_map.values()))
                db.commit()
                logger.info(f"Inserted {len(dc_order_map)} DataCo orders.")
        except Exception as e:
            logger.warning(f"DataCo ingestion skipped or failed: {e}")

        logger.info("Nexus dataset ingestion complete! Orders Agent database is ready.")

    except Exception as e:
        logger.error(f"Ingestion error: {e}", exc_info=True)
        if db:
            db.rollback()
        raise
    finally:
        if should_close and db:
            db.close()


valid_prods_cache = set()

if __name__ == "__main__":
    seed_data()
