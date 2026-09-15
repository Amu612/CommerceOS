"""
Nexus Dataset Ingestion Script — High Performance, Low-Memory Streaming.
Reads real Olist and DataCo transaction records (.csv or .csv.gz) and populates
the database with minimal memory footprint (safe for 1GB RAM EC2 free-tier instances).
"""

from __future__ import annotations

import contextlib
import csv
import gzip
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

# Add backend directory to sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from sqlalchemy.orm import Session

from app.database.session import SessionLocal, init_db
from app.models.dataco import DataCoOrder, DataCoOrderItem
from app.models.olist import (
    CategoryTranslation,
    Customer,
    Geolocation,
    Order,
    OrderItem,
    OrderPayment,
    OrderReview,
    Product,
    Seller,
)

logger = logging.getLogger("seed_nexus_data")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _get_nexus_data_dirs() -> list[str]:
    dirs: list[str] = []

    for env_var in ("DATASET_DIR", "NEXUS_DATA_DIR", "DATA_DIR"):
        candidate = os.environ.get(env_var)
        if candidate and os.path.isdir(candidate):
            dirs.append(candidate)

    try:
        from app.core.settings import settings

        if getattr(settings, "DATASET_DIR", None) and os.path.isdir(settings.DATASET_DIR):
            dirs.append(settings.DATASET_DIR)
    except Exception:
        pass

    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_dir = os.path.dirname(backend_dir)
    parent_dir = os.path.dirname(project_dir)

    for base in (backend_dir, project_dir, parent_dir):
        dirs.extend(
            [
                os.path.join(base, "data"),
                os.path.join(base, "data", "raw"),
                os.path.join(base, "data", "processed"),
                os.path.join(base, "backend", "data"),
                os.path.join(base, "backend", "data", "raw"),
                os.path.join(base, "backend", "data", "processed"),
                os.path.join(base, "backend", "app", "data"),
                os.path.join(base, "backend", "app", "data", "raw"),
                os.path.join(base, "backend", "app", "data", "processed"),
            ]
        )

    seen = set()
    valid_dirs = []
    for d in dirs:
        norm = os.path.normpath(os.path.abspath(d)) if d else ""
        if norm and norm not in seen and os.path.isdir(norm):
            seen.add(norm)
            valid_dirs.append(norm)

    return valid_dirs


def find_dataset(filename: str) -> str:
    for d in _get_nexus_data_dirs():
        p = os.path.join(d, filename)
        if os.path.exists(p):
            return p
        if os.path.exists(p + ".gz"):
            return p + ".gz"
        if filename.endswith(".csv"):
            gz_name = filename[:-4] + ".csv.gz"
            p_gz = os.path.join(d, gz_name)
            if os.path.exists(p_gz):
                return p_gz
    raise FileNotFoundError(
        f"Dataset {filename} (or .gz) not found in Nexus data paths: {_get_nexus_data_dirs()}"
    )


@contextlib.contextmanager
def open_dataset_reader(filename: str, encoding: str = "utf-8"):
    path = find_dataset(filename)
    if path.endswith(".gz"):
        with gzip.open(path, mode="rt", encoding=encoding, errors="replace") as f:
            yield csv.DictReader(f)
    else:
        with open(path, encoding=encoding, errors="replace") as f:
            yield csv.DictReader(f)


def parse_dt(v: Any) -> datetime | None:
    if not v:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "null", "nat"):
        return None
    try:
        if len(s) == 10:
            return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC)
        elif len(s) >= 19:
            return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except Exception:
        pass
    try:
        import pandas as pd

        dt = pd.to_datetime(s, errors="coerce")
        return dt.to_pydatetime().replace(tzinfo=UTC) if pd.notna(dt) else None
    except Exception:
        return None


def clean_str(v: Any, default: str | None = None) -> str | None:
    if v is None:
        return default
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return default
    return s


def clean_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        s = str(v).strip()
        if not s or s.lower() in ("nan", "none", "null"):
            return default
        return float(s)
    except Exception:
        return default


def clean_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        s = str(v).strip()
        if not s or s.lower() in ("nan", "none", "null"):
            return default
        return int(float(s))
    except Exception:
        return default


def seed_data(
    db: Session | None = None,
    olist_limit: int | None = None,
    dataco_limit: int | None = 15000,
    seed_orders: bool = True,
):
    should_close = False
    if db is None:
        init_db()
        db = SessionLocal()
        should_close = True

    try:
        logger.info("Starting streaming Nexus dataset ingestion into database...")

        # ── 1. Ingest Olist Customers ──
        existing_custs = {r[0] for r in db.query(Customer.customer_id).all()}
        cust_batch = []
        with open_dataset_reader("olist_customers_dataset.csv") as reader:
            for row in reader:
                if olist_limit and len(existing_custs) >= olist_limit:
                    break
                cid = clean_str(row.get("customer_id"))
                if not cid or cid in existing_custs:
                    continue
                existing_custs.add(cid)
                cust_batch.append(
                    {
                        "customer_id": cid,
                        "customer_unique_id": clean_str(row.get("customer_unique_id"), cid),
                        "customer_zip_code_prefix": clean_int(row.get("customer_zip_code_prefix")),
                        "customer_city": clean_str(row.get("customer_city"), "unknown"),
                        "customer_state": clean_str(row.get("customer_state"), "NA"),
                    }
                )
                if len(cust_batch) >= 2000:
                    db.bulk_insert_mappings(Customer, cust_batch)
                    db.commit()
                    cust_batch.clear()
        if cust_batch:
            db.bulk_insert_mappings(Customer, cust_batch)
            db.commit()
            cust_batch.clear()
        logger.info(f"Loaded Olist customers (total in DB: {len(existing_custs):,}).")

        # ── 2. Ingest Olist Products ──
        existing_prods = {r[0] for r in db.query(Product.product_id).all()}
        prod_batch = []
        with open_dataset_reader("olist_products_dataset.csv") as reader:
            for row in reader:
                pid = clean_str(row.get("product_id"))
                if not pid or pid in existing_prods:
                    continue
                existing_prods.add(pid)
                prod_batch.append(
                    {
                        "product_id": pid,
                        "product_category_name": clean_str(row.get("product_category_name")),
                        "product_name_lenght": clean_int(row.get("product_name_lenght"), None),
                        "product_description_lenght": clean_int(row.get("product_description_lenght"), None),
                        "product_photos_qty": clean_int(row.get("product_photos_qty"), None),
                        "product_weight_g": clean_float(row.get("product_weight_g"), None),
                        "product_length_cm": clean_float(row.get("product_length_cm"), None),
                        "product_height_cm": clean_float(row.get("product_height_cm"), None),
                        "product_width_cm": clean_float(row.get("product_width_cm"), None),
                    }
                )
                if len(prod_batch) >= 2000:
                    db.bulk_insert_mappings(Product, prod_batch)
                    db.commit()
                    prod_batch.clear()
        if prod_batch:
            db.bulk_insert_mappings(Product, prod_batch)
            db.commit()
            prod_batch.clear()
        logger.info(f"Loaded Olist products (total in DB: {len(existing_prods):,}).")

        # ── 3. Ingest Olist Sellers ──
        existing_sellers = {r[0] for r in db.query(Seller.seller_id).all()}
        sell_batch = []
        with open_dataset_reader("olist_sellers_dataset.csv") as reader:
            for row in reader:
                sid = clean_str(row.get("seller_id"))
                if not sid or sid in existing_sellers:
                    continue
                existing_sellers.add(sid)
                sell_batch.append(
                    {
                        "seller_id": sid,
                        "seller_zip_code_prefix": clean_int(row.get("seller_zip_code_prefix")),
                        "seller_city": clean_str(row.get("seller_city"), "unknown"),
                        "seller_state": clean_str(row.get("seller_state"), "NA"),
                    }
                )
                if len(sell_batch) >= 2000:
                    db.bulk_insert_mappings(Seller, sell_batch)
                    db.commit()
                    sell_batch.clear()
        if sell_batch:
            db.bulk_insert_mappings(Seller, sell_batch)
            db.commit()
            sell_batch.clear()
        logger.info(f"Loaded Olist sellers (total in DB: {len(existing_sellers):,}).")

        # ── 4. Category Translations ──
        try:
            existing_ct = {r[0] for r in db.query(CategoryTranslation.product_category_name).all()}
            ct_batch = []
            with open_dataset_reader("product_category_name_translation.csv") as reader:
                for row in reader:
                    pt = clean_str(row.get("product_category_name"))
                    en = clean_str(row.get("product_category_name_english"))
                    if pt and pt not in existing_ct:
                        existing_ct.add(pt)
                        ct_batch.append(
                            {
                                "product_category_name": pt,
                                "product_category_name_english": en or pt,
                            }
                        )
            if ct_batch:
                db.bulk_insert_mappings(CategoryTranslation, ct_batch)
                db.commit()
            logger.info(f"Loaded category translations ({len(existing_ct)} categories).")
        except Exception as e:
            logger.warning(f"Category translation seed note: {e}")

        # ── 5. Ingest Geolocation Reference (Aggregated by unique ZIP prefix for 3D Route Intelligence) ──
        try:
            geo_count = db.query(Geolocation.id).count()
            if geo_count == 0:
                logger.info(
                    "Aggregating unique geolocation ZIP prefixes from olist_geolocation_dataset.csv..."
                )
                geo_map: dict[int, dict[str, Any]] = {}
                with open_dataset_reader("olist_geolocation_dataset.csv") as reader:
                    for row in reader:
                        pfx = clean_int(row.get("geolocation_zip_code_prefix"))
                        if pfx and pfx not in geo_map:
                            geo_map[pfx] = {
                                "geolocation_zip_code_prefix": pfx,
                                "geolocation_lat": clean_float(row.get("geolocation_lat")),
                                "geolocation_lng": clean_float(row.get("geolocation_lng")),
                                "geolocation_city": clean_str(row.get("geolocation_city"), "unknown"),
                                "geolocation_state": clean_str(row.get("geolocation_state"), "NA"),
                            }
                if geo_map:
                    records = list(geo_map.values())
                    for i in range(0, len(records), 2000):
                        db.bulk_insert_mappings(Geolocation, records[i : i + 2000])
                        db.commit()
                    logger.info(f"Inserted {len(records):,} Geolocation reference coordinates.")
            else:
                logger.info(f"Geolocation table already has {geo_count:,} coordinates.")
        except Exception as e:
            logger.warning(f"Geolocation seed note: {e}")

        if not seed_orders:
            logger.info("seed_orders is False — static dimensions seeded. Skipping orders.")
            return

        # ── 6. Ingest Olist Orders ──
        existing_orders = {r[0] for r in db.query(Order.order_id).all()}
        order_batch = []
        with open_dataset_reader("olist_orders_dataset.csv") as reader:
            for row in reader:
                if olist_limit and len(existing_orders) >= olist_limit:
                    break
                oid = clean_str(row.get("order_id"))
                if not oid or oid in existing_orders:
                    continue
                cid = clean_str(row.get("customer_id"))
                if cid and cid not in existing_custs:
                    existing_custs.add(cid)
                    db.add(
                        Customer(
                            customer_id=cid,
                            customer_unique_id=cid,
                            customer_zip_code_prefix=1000,
                            customer_city="Sao Paulo",
                            customer_state="SP",
                        )
                    )
                    db.commit()

                purch_dt = parse_dt(row.get("order_purchase_timestamp")) or datetime.now(UTC)
                existing_orders.add(oid)
                order_batch.append(
                    {
                        "order_id": oid,
                        "customer_id": cid,
                        "order_status": clean_str(row.get("order_status"), "delivered"),
                        "order_purchase_timestamp": purch_dt,
                        "order_approved_at": parse_dt(row.get("order_approved_at")),
                        "order_delivered_carrier_date": parse_dt(row.get("order_delivered_carrier_date")),
                        "order_delivered_customer_date": parse_dt(row.get("order_delivered_customer_date")),
                        "order_estimated_delivery_date": parse_dt(row.get("order_estimated_delivery_date"))
                        or purch_dt,
                    }
                )
                if len(order_batch) >= 2000:
                    db.bulk_insert_mappings(Order, order_batch)
                    db.commit()
                    order_batch.clear()
        if order_batch:
            db.bulk_insert_mappings(Order, order_batch)
            db.commit()
            order_batch.clear()
        logger.info(f"Loaded Olist orders (total in DB: {len(existing_orders):,}).")

        # ── 7. Ingest Olist Order Items ──
        existing_items = {(r[0], r[1]) for r in db.query(OrderItem.order_id, OrderItem.order_item_id).all()}
        item_batch = []
        with open_dataset_reader("olist_order_items_dataset.csv") as reader:
            for row in reader:
                oid = clean_str(row.get("order_id"))
                seq = clean_int(row.get("order_item_id"), 1)
                if not oid or oid not in existing_orders or (oid, seq) in existing_items:
                    continue
                pid = clean_str(row.get("product_id"))
                if pid and pid not in existing_prods:
                    existing_prods.add(pid)
                    db.add(Product(product_id=pid, product_category_name="general"))
                    db.commit()
                sid = clean_str(row.get("seller_id"))
                if sid and sid not in existing_sellers:
                    existing_sellers.add(sid)
                    db.add(
                        Seller(
                            seller_id=sid,
                            seller_zip_code_prefix=0,
                            seller_city="unknown",
                            seller_state="NA",
                        )
                    )
                    db.commit()

                existing_items.add((oid, seq))
                item_batch.append(
                    {
                        "order_id": oid,
                        "order_item_id": seq,
                        "product_id": pid,
                        "seller_id": sid,
                        "shipping_limit_date": parse_dt(row.get("shipping_limit_date")) or datetime.now(UTC),
                        "price": clean_float(row.get("price")),
                        "freight_value": clean_float(row.get("freight_value")),
                    }
                )
                if len(item_batch) >= 2000:
                    db.bulk_insert_mappings(OrderItem, item_batch)
                    db.commit()
                    item_batch.clear()
        if item_batch:
            db.bulk_insert_mappings(OrderItem, item_batch)
            db.commit()
            item_batch.clear()
        logger.info(f"Loaded Olist order items (total in DB: {len(existing_items):,}).")

        # ── 8. Ingest Olist Order Payments ──
        try:
            existing_pay = {
                (r[0], r[1]) for r in db.query(OrderPayment.order_id, OrderPayment.payment_sequential).all()
            }
            pay_batch = []
            with open_dataset_reader("olist_order_payments_dataset.csv") as reader:
                for row in reader:
                    oid = clean_str(row.get("order_id"))
                    seq = clean_int(row.get("payment_sequential"), 1)
                    if not oid or oid not in existing_orders or (oid, seq) in existing_pay:
                        continue
                    existing_pay.add((oid, seq))
                    pay_batch.append(
                        {
                            "order_id": oid,
                            "payment_sequential": seq,
                            "payment_type": clean_str(row.get("payment_type"), "credit_card"),
                            "payment_installments": clean_int(row.get("payment_installments"), 1),
                            "payment_value": clean_float(row.get("payment_value")),
                        }
                    )
                    if len(pay_batch) >= 2000:
                        db.bulk_insert_mappings(OrderPayment, pay_batch)
                        db.commit()
                        pay_batch.clear()
            if pay_batch:
                db.bulk_insert_mappings(OrderPayment, pay_batch)
                db.commit()
                pay_batch.clear()
            logger.info(f"Loaded Olist order payments (total in DB: {len(existing_pay):,}).")
        except Exception as e:
            logger.warning(f"Order payment seed note: {e}")

        # ── 9. Ingest Olist Order Reviews ──
        try:
            existing_rev = {(r[0], r[1]) for r in db.query(OrderReview.review_id, OrderReview.order_id).all()}
            rev_batch = []
            with open_dataset_reader("olist_order_reviews_dataset.csv") as reader:
                for row in reader:
                    rid = clean_str(row.get("review_id"))
                    oid = clean_str(row.get("order_id"))
                    if not rid or not oid or oid not in existing_orders or (rid, oid) in existing_rev:
                        continue
                    existing_rev.add((rid, oid))
                    rev_batch.append(
                        {
                            "review_id": rid,
                            "order_id": oid,
                            "review_score": clean_int(row.get("review_score"), 3),
                            "review_comment_title": clean_str(row.get("review_comment_title")),
                            "review_comment_message": clean_str(row.get("review_comment_message")),
                            "review_creation_date": parse_dt(row.get("review_creation_date"))
                            or datetime.now(UTC),
                            "review_answer_timestamp": parse_dt(row.get("review_answer_timestamp")),
                        }
                    )
                    if len(rev_batch) >= 2000:
                        db.bulk_insert_mappings(OrderReview, rev_batch)
                        db.commit()
                        rev_batch.clear()
            if rev_batch:
                db.bulk_insert_mappings(OrderReview, rev_batch)
                db.commit()
                rev_batch.clear()
            logger.info(f"Loaded Olist order reviews (total in DB: {len(existing_rev):,}).")
        except Exception as e:
            logger.warning(f"Order review seed note: {e}")

        # ── 10. Ingest DataCo Orders ──
        try:
            existing_dc_orders = {r[0] for r in db.query(DataCoOrder.order_id).all()}
            existing_dc_items = {r[0] for r in db.query(DataCoOrderItem.order_item_id).all()}
            dc_order_map = {}
            dc_item_batch = []

            with open_dataset_reader("DataCoSupplyChainDataset.csv", encoding="latin1") as reader:
                for row in reader:
                    if dataco_limit and len(existing_dc_orders) + len(dc_order_map) >= dataco_limit:
                        break
                    oid_raw = row.get("Order Id")
                    if not oid_raw:
                        continue
                    oid = clean_int(oid_raw)
                    item_id = clean_int(row.get("Order Item Id"))
                    if not oid or not item_id:
                        continue

                    if oid not in existing_dc_orders and oid not in dc_order_map:
                        order_dt = parse_dt(row.get("order date (DateOrders)")) or datetime.now(UTC)
                        ship_dt = parse_dt(row.get("shipping date (DateOrders)"))
                        dc_order_map[oid] = {
                            "order_id": oid,
                            "customer_id": clean_int(row.get("Order Customer Id")),
                            "customer_segment": clean_str(row.get("Customer Segment")),
                            "customer_city": clean_str(row.get("Customer City")),
                            "customer_state": clean_str(row.get("Customer State")),
                            "customer_country": clean_str(row.get("Customer Country")),
                            "market": clean_str(row.get("Market")),
                            "order_region": clean_str(row.get("Order Region")),
                            "order_country": clean_str(row.get("Order Country")),
                            "order_city": clean_str(row.get("Order City")),
                            "order_date": order_dt,
                            "shipping_date": ship_dt,
                            "order_status": clean_str(row.get("Order Status"), "COMPLETE"),
                            "shipping_mode": clean_str(row.get("Shipping Mode"), "Standard Class"),
                            "delivery_status": clean_str(row.get("Delivery Status"), "Standard"),
                            "late_delivery_risk": clean_int(row.get("Late_delivery_risk"), 0),
                            "days_for_shipping_real": clean_float(row.get("Days for shipping (real)"), None),
                            "days_for_shipment_scheduled": clean_float(
                                row.get("Days for shipment (scheduled)"), None
                            ),
                            "payment_type": clean_str(row.get("Type")),
                            "order_total": clean_float(row.get("Order Item Total")),
                            "order_profit": clean_float(row.get("Order Profit Per Order")),
                            "source": "dataco",
                        }
                    elif oid in dc_order_map:
                        dc_order_map[oid]["order_total"] += clean_float(row.get("Order Item Total"))
                        dc_order_map[oid]["order_profit"] += clean_float(row.get("Order Profit Per Order"))

                    if item_id not in existing_dc_items:
                        existing_dc_items.add(item_id)
                        dc_item_batch.append(
                            {
                                "order_item_id": item_id,
                                "order_id": oid,
                                "product_card_id": clean_int(row.get("Product Card Id")),
                                "product_name": clean_str(row.get("Product Name"), ""),
                                "category_id": clean_int(row.get("Category Id"), None),
                                "category_name": clean_str(row.get("Category Name"), ""),
                                "department_id": clean_int(row.get("Department Id"), None),
                                "department_name": clean_str(row.get("Department Name"), ""),
                                "product_price": clean_float(row.get("Product Price")),
                                "order_item_quantity": clean_int(row.get("Order Item Quantity"), 1),
                                "sales": clean_float(row.get("Sales")),
                                "order_item_discount": clean_float(row.get("Order Item Discount")),
                                "order_item_discount_rate": clean_float(row.get("Order Item Discount Rate")),
                                "order_item_total": clean_float(row.get("Order Item Total")),
                                "order_item_profit_ratio": clean_float(row.get("Order Item Profit Ratio")),
                                "order_profit_per_order": clean_float(row.get("Order Profit Per Order")),
                            }
                        )

            if dc_order_map:
                records = list(dc_order_map.values())
                for i in range(0, len(records), 2000):
                    db.bulk_insert_mappings(DataCoOrder, records[i : i + 2000])
                    db.commit()
                logger.info(f"Loaded {len(records):,} DataCo orders.")

            if dc_item_batch:
                for i in range(0, len(dc_item_batch), 2000):
                    db.bulk_insert_mappings(DataCoOrderItem, dc_item_batch[i : i + 2000])
                    db.commit()
                logger.info(f"Loaded {len(dc_item_batch):,} DataCo order items.")
        except Exception as e:
            logger.warning(f"DataCo ingestion note: {e}")

        logger.info("Dataset ingestion successfully completed! All tables ready.")

    except Exception as e:
        logger.error(f"Ingestion error: {e}", exc_info=True)
        if db:
            db.rollback()
        raise
    finally:
        if should_close and db:
            db.close()


if __name__ == "__main__":
    seed_data(seed_orders=True)
