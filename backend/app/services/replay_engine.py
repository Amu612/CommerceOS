import asyncio
import logging
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

import pandas as pd
from sqlalchemy.orm import Session

from app.database.session import SessionLocal
from app.models.dataco import DataCoOrder, DataCoOrderItem
from app.models.olist import (
    Customer,
    Geolocation,
    Order,
    OrderItem,
    OrderPayment,
    OrderReview,
    Product,
    Seller,
)
from app.services.event_bus import OperationalEvent, event_bus, publish_event
from app.services.state_service import state_service

logger = logging.getLogger(__name__)


def _get_nexus_data_dirs() -> list[str]:
    """
    Discovers dataset directories strictly from environment variables,
    application settings, and standard project-relative data folders.
    Self-contained: does not depend on any hardcoded external paths or references.
    """
    dirs: list[str] = []

    # 1. Environment variables
    for env_var in ("DATASET_DIR", "NEXUS_DATA_DIR", "DATA_DIR"):
        candidate = os.environ.get(env_var)
        if candidate and os.path.isdir(candidate):
            dirs.append(candidate)

    # 2. Application settings fallback
    try:
        from app.core.settings import settings

        if getattr(settings, "DATASET_DIR", None) and os.path.isdir(settings.DATASET_DIR):
            dirs.append(settings.DATASET_DIR)
    except Exception:
        pass

    # 3. Standard project-relative locations
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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


def find_dataset(filename: str) -> str | None:
    """Finds a dataset CSV file across project data directories."""
    for d in _get_nexus_data_dirs():
        p = os.path.join(d, filename)
        if os.path.exists(p):
            return p
    return None


class ReplayEngine:
    """
    Deterministic Chronological Event Replay & Dynamic Ingestion Engine.
    Controls the flow of order records into the system (Start, Pause, Resume, Reset, Speed).
    Synchronously updates both in-memory state and the database so the Orders Agent
    strictly analyzes the data ingested up to the current point in time.
    """

    SUPPORTED_SPEEDS: ClassVar[list[int]] = [1, 5, 10, 50, 60, 200, 300]

    def __init__(self):
        self.status: str = "stopped"  # "stopped", "running", "paused"
        self.speed: int = 2  # events per second base (slow, visible streaming by default)
        self.events_processed: int = 0
        self.events_remaining: int = 0
        self.current_simulated_date: datetime | None = None
        self.last_event_timestamp: datetime | None = None

        self._events_index: list[dict[str, Any]] = []
        self._master_events_index: list[dict[str, Any]] = []
        self._current_index: int = 0
        self._replay_task: asyncio.Task | None = None
        self._is_indexed: bool = False

        # Metadata caches loaded from raw datasets or database to ensure real attributes
        self._customer_cache: dict[str, dict[str, Any]] = {}
        self._product_cache: dict[str, dict[str, Any]] = {}
        self._seller_cache: dict[str, dict[str, Any]] = {}

        # Concurrency lock ensuring atomic batch execution
        self._ingest_lock = threading.Lock()

    def _seed_geolocation_reference(self) -> None:
        """
        Loads olist_geolocation_dataset.csv into the `geolocation` reference table
        (median lat/lng per ZIP prefix — the same aggregation the logistics route
        lookup expects). Idempotent: skipped when the table already has rows.
        """
        from sqlalchemy import func

        from app.database.session import SessionLocal

        db = SessionLocal()
        try:
            existing = db.query(func.count(Geolocation.id)).scalar() or 0
            if existing > 0:
                return
            geo_path = find_dataset("olist_geolocation_dataset.csv")
            if not geo_path:
                logger.warning("geolocation_seed_skipped: olist_geolocation_dataset.csv not found")
                return
            df_geo = pd.read_csv(geo_path)
            needed = {
                "geolocation_zip_code_prefix",
                "geolocation_lat",
                "geolocation_lng",
                "geolocation_city",
                "geolocation_state",
            }
            if not needed.issubset(df_geo.columns):
                logger.warning(f"geolocation_seed_skipped: unexpected columns {list(df_geo.columns)}")
                return
            agg = (
                df_geo.groupby("geolocation_zip_code_prefix")
                .agg(
                    geolocation_lat=("geolocation_lat", "median"),
                    geolocation_lng=("geolocation_lng", "median"),
                    geolocation_city=("geolocation_city", "first"),
                    geolocation_state=("geolocation_state", "first"),
                )
                .reset_index()
            )
            records = [
                {
                    "geolocation_zip_code_prefix": int(r.geolocation_zip_code_prefix),
                    "geolocation_lat": float(r.geolocation_lat),
                    "geolocation_lng": float(r.geolocation_lng),
                    "geolocation_city": str(r.geolocation_city),
                    "geolocation_state": str(r.geolocation_state),
                }
                for r in agg.itertuples(index=False)
            ]
            db.bulk_insert_mappings(Geolocation, records)
            db.commit()
            logger.info(f"geolocation_seeded: {len(records):,} ZIP-prefix medians from {geo_path}")
        except Exception as exc:
            db.rollback()
            logger.warning(f"geolocation_seed_failed: {exc}")
        finally:
            db.close()

    def index_events_from_datasets(self, max_orders: int = 5000) -> int:
        """Loads and sorts historical orders chronologically from Nexus raw CSVs."""
        logger.info(f"Indexing Nexus order records (limit: {max_orders})...")
        start_time = time.time()
        self._seed_geolocation_reference()
        raw_events: list[dict[str, Any]] = []

        try:
            # 1. Load Olist Orders
            orders_path = find_dataset("olist_orders_dataset.csv")
            olist_order_rows = []
            target_order_ids: set[str] = set()
            target_customer_ids: set[str] = set()

            if orders_path:
                df_orders = pd.read_csv(orders_path, nrows=max_orders)
                for col in [
                    "order_purchase_timestamp",
                    "order_approved_at",
                    "order_delivered_carrier_date",
                    "order_delivered_customer_date",
                    "order_estimated_delivery_date",
                ]:
                    if col in df_orders.columns:
                        df_orders[col] = pd.to_datetime(df_orders[col], errors="coerce")

                for _, row in df_orders.iterrows():
                    oid = str(row["order_id"])
                    cid = str(row["customer_id"])
                    target_order_ids.add(oid)
                    target_customer_ids.add(cid)
                    olist_order_rows.append(row)

            # 2. Load Real Customer Data for target customers
            cust_path = find_dataset("olist_customers_dataset.csv")
            if cust_path:
                try:
                    df_cust = pd.read_csv(cust_path)
                    for _, row in df_cust.iterrows():
                        cid = str(row["customer_id"])
                        if cid in target_customer_ids or len(self._customer_cache) < 20000:
                            self._customer_cache[cid] = {
                                "customer_id": cid,
                                "customer_unique_id": str(row.get("customer_unique_id") or cid),
                                "customer_zip_code_prefix": (
                                    int(row["customer_zip_code_prefix"])
                                    if pd.notna(row.get("customer_zip_code_prefix"))
                                    else 1000
                                ),
                                "customer_city": str(row.get("customer_city") or "Sao Paulo"),
                                "customer_state": str(row.get("customer_state") or "SP"),
                            }
                except Exception as ce:
                    logger.warning(f"Failed loading customer metadata: {ce}")

            # 3. Load Olist Order Items for indexed orders
            items_path = find_dataset("olist_order_items_dataset.csv")
            olist_items_by_order: dict[str, list[dict[str, Any]]] = {}
            target_product_ids: set[str] = set()
            target_seller_ids: set[str] = set()

            if items_path and target_order_ids:
                try:
                    df_items = pd.read_csv(items_path)
                    df_items = df_items[df_items["order_id"].astype(str).isin(target_order_ids)]
                    df_items["shipping_limit_date"] = pd.to_datetime(
                        df_items["shipping_limit_date"], errors="coerce"
                    )
                    for _, row in df_items.iterrows():
                        oid = str(row["order_id"])
                        pid = str(row["product_id"])
                        sid = str(row["seller_id"])
                        target_product_ids.add(pid)
                        target_seller_ids.add(sid)
                        if oid not in olist_items_by_order:
                            olist_items_by_order[oid] = []
                        olist_items_by_order[oid].append(
                            {
                                "product_id": pid,
                                "seller_id": sid,
                                "shipping_limit_date": (
                                    row["shipping_limit_date"].to_pydatetime()
                                    if pd.notna(row["shipping_limit_date"])
                                    else None
                                ),
                                "price": float(row["price"]) if pd.notna(row.get("price")) else 0.0,
                                "freight_value": (
                                    float(row["freight_value"]) if pd.notna(row.get("freight_value")) else 0.0
                                ),
                            }
                        )
                except Exception as ie:
                    logger.warning(f"Failed loading order items: {ie}")

            # 4. Load Real Product Metadata
            prod_path = find_dataset("olist_products_dataset.csv")
            if prod_path:
                try:
                    df_prod = pd.read_csv(prod_path)
                    for _, row in df_prod.iterrows():
                        pid = str(row["product_id"])
                        if pid in target_product_ids or len(self._product_cache) < 20000:
                            self._product_cache[pid] = {
                                "product_id": pid,
                                "product_category_name": (
                                    str(row.get("product_category_name"))
                                    if pd.notna(row.get("product_category_name"))
                                    else "general"
                                ),
                                "product_name_lenght": (
                                    int(row["product_name_lenght"])
                                    if pd.notna(row.get("product_name_lenght"))
                                    else None
                                ),
                                "product_description_lenght": (
                                    int(row["product_description_lenght"])
                                    if pd.notna(row.get("product_description_lenght"))
                                    else None
                                ),
                                "product_photos_qty": (
                                    int(row["product_photos_qty"])
                                    if pd.notna(row.get("product_photos_qty"))
                                    else None
                                ),
                                "product_weight_g": (
                                    float(row["product_weight_g"])
                                    if pd.notna(row.get("product_weight_g"))
                                    else None
                                ),
                                "product_length_cm": (
                                    float(row["product_length_cm"])
                                    if pd.notna(row.get("product_length_cm"))
                                    else None
                                ),
                                "product_height_cm": (
                                    float(row["product_height_cm"])
                                    if pd.notna(row.get("product_height_cm"))
                                    else None
                                ),
                                "product_width_cm": (
                                    float(row["product_width_cm"])
                                    if pd.notna(row.get("product_width_cm"))
                                    else None
                                ),
                            }
                except Exception as pe:
                    logger.warning(f"Failed loading product metadata: {pe}")

            # 5. Load Real Seller Metadata
            sell_path = find_dataset("olist_sellers_dataset.csv")
            if sell_path:
                try:
                    df_sell = pd.read_csv(sell_path)
                    for _, row in df_sell.iterrows():
                        sid = str(row["seller_id"])
                        if sid in target_seller_ids or len(self._seller_cache) < 10000:
                            self._seller_cache[sid] = {
                                "seller_id": sid,
                                "seller_zip_code_prefix": (
                                    int(row["seller_zip_code_prefix"])
                                    if pd.notna(row.get("seller_zip_code_prefix"))
                                    else 0
                                ),
                                "seller_city": str(row.get("seller_city") or "unknown"),
                                "seller_state": str(row.get("seller_state") or "NA"),
                            }
                except Exception as se:
                    logger.warning(f"Failed loading seller metadata: {se}")

            # 6. Load Olist Order Payments
            pay_path = find_dataset("olist_order_payments_dataset.csv")
            olist_payments_by_order: dict[str, list[dict[str, Any]]] = {}
            if pay_path and target_order_ids:
                try:
                    df_pay = pd.read_csv(pay_path)
                    df_pay = df_pay[df_pay["order_id"].astype(str).isin(target_order_ids)]
                    for _, row in df_pay.iterrows():
                        oid = str(row["order_id"])
                        if oid not in olist_payments_by_order:
                            olist_payments_by_order[oid] = []
                        olist_payments_by_order[oid].append(
                            {
                                "order_id": oid,
                                "payment_sequential": (
                                    int(row["payment_sequential"])
                                    if pd.notna(row.get("payment_sequential"))
                                    else 1
                                ),
                                "payment_type": str(row.get("payment_type") or "credit_card"),
                                "payment_installments": (
                                    int(row["payment_installments"])
                                    if pd.notna(row.get("payment_installments"))
                                    else 1
                                ),
                                "payment_value": (
                                    float(row["payment_value"]) if pd.notna(row.get("payment_value")) else 0.0
                                ),
                            }
                        )
                except Exception as pye:
                    logger.warning(f"Failed loading order payments: {pye}")

            # 7. Load Olist Order Reviews
            rev_path = find_dataset("olist_order_reviews_dataset.csv")
            olist_reviews_by_order: dict[str, list[dict[str, Any]]] = {}
            if rev_path and target_order_ids:
                try:
                    df_rev = pd.read_csv(rev_path)
                    df_rev = df_rev[df_rev["order_id"].astype(str).isin(target_order_ids)]
                    for c in ("review_creation_date", "review_answer_timestamp"):
                        if c in df_rev.columns:
                            df_rev[c] = pd.to_datetime(df_rev[c], errors="coerce")
                    for _, row in df_rev.iterrows():
                        oid = str(row["order_id"])
                        if oid not in olist_reviews_by_order:
                            olist_reviews_by_order[oid] = []
                        olist_reviews_by_order[oid].append(
                            {
                                "review_id": str(row["review_id"]),
                                "order_id": oid,
                                "review_score": (
                                    int(row["review_score"]) if pd.notna(row.get("review_score")) else 3
                                ),
                                "review_comment_title": (
                                    str(row["review_comment_title"])
                                    if pd.notna(row.get("review_comment_title"))
                                    else None
                                ),
                                "review_comment_message": (
                                    str(row["review_comment_message"])
                                    if pd.notna(row.get("review_comment_message"))
                                    else None
                                ),
                                "review_creation_date": (
                                    row["review_creation_date"].to_pydatetime()
                                    if pd.notna(row.get("review_creation_date"))
                                    else None
                                ),
                                "review_answer_timestamp": (
                                    row["review_answer_timestamp"].to_pydatetime()
                                    if pd.notna(row.get("review_answer_timestamp"))
                                    else None
                                ),
                            }
                        )
                except Exception as rve:
                    logger.warning(f"Failed loading order reviews: {rve}")

            # 8. Build Olist Event Records
            seen_olist_ids = set()
            for row in olist_order_rows:
                purch_dt = row["order_purchase_timestamp"]
                if pd.isna(purch_dt):
                    continue
                purch_ts = purch_dt.to_pydatetime()
                if purch_ts.tzinfo is None:
                    purch_ts = purch_ts.replace(tzinfo=UTC)

                oid = str(row["order_id"])
                if oid in seen_olist_ids:
                    continue
                seen_olist_ids.add(oid)

                cid = str(row["customer_id"])
                status = str(row.get("order_status", "delivered"))

                est_dt = row["order_estimated_delivery_date"]
                deliv_dt = row["order_delivered_customer_date"]
                is_delayed = bool(pd.notna(deliv_dt) and pd.notna(est_dt) and deliv_dt > est_dt)

                items = olist_items_by_order.get(oid, [])
                total_price = sum(item["price"] for item in items) if items else 0.0
                total_freight = sum(item["freight_value"] for item in items) if items else 0.0
                item_count = len(items) if items else 1

                payments = olist_payments_by_order.get(oid, [])
                reviews = olist_reviews_by_order.get(oid, [])
                payment_total = (
                    sum(p["payment_value"] for p in payments) if payments else (total_price + total_freight)
                )

                # Real customer metadata
                cust_meta = self._customer_cache.get(
                    cid,
                    {
                        "customer_id": cid,
                        "customer_unique_id": cid,
                        "customer_zip_code_prefix": 1000,
                        "customer_city": "Sao Paulo",
                        "customer_state": "SP",
                    },
                )

                raw_events.append(
                    {
                        "source": "olist",
                        "timestamp": purch_ts,
                        "order_id": oid,
                        "customer_id": cid,
                        "customer_unique_id": cust_meta["customer_unique_id"],
                        "customer_meta": cust_meta,
                        "status": status,
                        "purchase_timestamp": purch_ts,
                        "approved_at": (
                            row["order_approved_at"].to_pydatetime()
                            if pd.notna(row.get("order_approved_at"))
                            else None
                        ),
                        "delivered_carrier_date": (
                            row["order_delivered_carrier_date"].to_pydatetime()
                            if pd.notna(row.get("order_delivered_carrier_date"))
                            else None
                        ),
                        "delivered_customer_date": deliv_dt.to_pydatetime() if pd.notna(deliv_dt) else None,
                        "estimated_delivery_date": (
                            est_dt.to_pydatetime() if pd.notna(est_dt) else purch_ts + timedelta(days=7)
                        ),
                        "is_delayed": is_delayed,
                        "merchandise_revenue": total_price,
                        "freight_value": total_freight,
                        "payment_total": payment_total,
                        "item_count": item_count,
                        "order_items": items,
                        "order_payments": payments,
                        "order_reviews": reviews,
                    }
                )

            # 9. Load DataCo Order Items & Orders
            dc_path = find_dataset("DataCoSupplyChainDataset.csv")
            dc_items_by_order: dict[int, list[dict[str, Any]]] = {}
            if dc_path:
                try:
                    dc_item_cols = [
                        "Order Id",
                        "Order Item Id",
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
                        "Order Profit Per Order",
                    ]
                    df_dc_items = pd.read_csv(
                        dc_path, usecols=dc_item_cols, nrows=max_orders * 3, encoding="latin1"
                    )
                    for _, row in df_dc_items.iterrows():
                        oid = int(row["Order Id"])
                        if oid not in dc_items_by_order:
                            dc_items_by_order[oid] = []
                        dc_items_by_order[oid].append(
                            {
                                "order_item_id": int(row["Order Item Id"]),
                                "product_card_id": int(row["Product Card Id"]),
                                "product_name": str(row.get("Product Name", "")),
                                "category_id": (
                                    int(row["Category Id"]) if pd.notna(row.get("Category Id")) else None
                                ),
                                "category_name": str(row.get("Category Name", "")),
                                "department_id": (
                                    int(row["Department Id"]) if pd.notna(row.get("Department Id")) else None
                                ),
                                "department_name": str(row.get("Department Name", "")),
                                "product_price": (
                                    float(row["Product Price"]) if pd.notna(row.get("Product Price")) else 0.0
                                ),
                                "order_item_quantity": (
                                    int(row["Order Item Quantity"])
                                    if pd.notna(row.get("Order Item Quantity"))
                                    else 1
                                ),
                                "sales": float(row["Sales"]) if pd.notna(row.get("Sales")) else 0.0,
                                "order_item_discount": (
                                    float(row["Order Item Discount"])
                                    if pd.notna(row.get("Order Item Discount"))
                                    else 0.0
                                ),
                                "order_item_discount_rate": (
                                    float(row["Order Item Discount Rate"])
                                    if pd.notna(row.get("Order Item Discount Rate"))
                                    else 0.0
                                ),
                                "order_item_total": (
                                    float(row["Order Item Total"])
                                    if pd.notna(row.get("Order Item Total"))
                                    else 0.0
                                ),
                                "order_item_profit_ratio": (
                                    float(row["Order Item Profit Ratio"])
                                    if pd.notna(row.get("Order Item Profit Ratio"))
                                    else 0.0
                                ),
                                "order_profit_per_order": (
                                    float(row["Order Profit Per Order"])
                                    if pd.notna(row.get("Order Profit Per Order"))
                                    else 0.0
                                ),
                            }
                        )
                except Exception as dcie:
                    logger.warning(f"Failed loading DataCo order items: {dcie}")

            # 10. Load DataCo Orders
            if dc_path:
                try:
                    usecols = [
                        "Order Id",
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
                        "Order Item Total",
                        "Order Profit Per Order",
                    ]
                    df_dc = pd.read_csv(dc_path, usecols=usecols, nrows=max_orders // 2, encoding="latin1")
                    df_dc["order date (DateOrders)"] = pd.to_datetime(
                        df_dc["order date (DateOrders)"], errors="coerce"
                    )
                    df_dc["shipping date (DateOrders)"] = pd.to_datetime(
                        df_dc["shipping date (DateOrders)"], errors="coerce"
                    )

                    seen_dc_ids = set()
                    for _, row in df_dc.iterrows():
                        odt = row["order date (DateOrders)"]
                        if pd.isna(odt):
                            continue
                        order_ts = odt.to_pydatetime()
                        if order_ts.tzinfo is None:
                            order_ts = order_ts.replace(tzinfo=UTC)

                        oid = int(row["Order Id"])
                        if oid in seen_dc_ids:
                            continue
                        seen_dc_ids.add(oid)

                        cid = int(row["Order Customer Id"]) if pd.notna(row.get("Order Customer Id")) else 0
                        status = str(row.get("Order Status") or "COMPLETE").strip()
                        late_risk = (
                            int(row["Late_delivery_risk"]) if pd.notna(row.get("Late_delivery_risk")) else 0
                        )

                        dc_items = dc_items_by_order.get(oid, [])
                        item_count = len(dc_items) if dc_items else 1

                        # Calculate true order total and profit from items if available
                        if dc_items:
                            total = sum(it["order_item_total"] for it in dc_items)
                            profit = sum(it["order_profit_per_order"] for it in dc_items)
                        else:
                            total = (
                                float(row["Order Item Total"])
                                if pd.notna(row.get("Order Item Total"))
                                else 50.0
                            )
                            profit = (
                                float(row["Order Profit Per Order"])
                                if pd.notna(row.get("Order Profit Per Order"))
                                else 5.0
                            )

                        ship_dt = row["shipping date (DateOrders)"]

                        raw_events.append(
                            {
                                "source": "dataco",
                                "timestamp": order_ts,
                                "order_id": oid,
                                "customer_id": cid,
                                "customer_segment": str(row.get("Customer Segment", "")),
                                "customer_city": str(row.get("Customer City", "")),
                                "customer_state": str(row.get("Customer State", "")),
                                "customer_country": str(row.get("Customer Country", "")),
                                "market": str(row.get("Market", "")),
                                "order_region": str(row.get("Order Region", "")),
                                "order_country": str(row.get("Order Country", "")),
                                "order_city": str(row.get("Order City", "")),
                                "order_date": order_ts,
                                "shipping_date": ship_dt.to_pydatetime() if pd.notna(ship_dt) else None,
                                "order_status": status,
                                "shipping_mode": str(row.get("Shipping Mode") or "Standard Class"),
                                "delivery_status": str(row.get("Delivery Status") or "Standard"),
                                "late_delivery_risk": late_risk,
                                "days_for_shipping_real": (
                                    float(row["Days for shipping (real)"])
                                    if pd.notna(row.get("Days for shipping (real)"))
                                    else None
                                ),
                                "days_for_shipment_scheduled": (
                                    float(row["Days for shipment (scheduled)"])
                                    if pd.notna(row.get("Days for shipment (scheduled)"))
                                    else None
                                ),
                                "payment_type": str(row.get("Type") or "DEBIT"),
                                "order_total": total,
                                "order_profit": profit,
                                "status": status,
                                "is_delayed": late_risk == 1,
                                "merchandise_revenue": total,
                                "freight_value": 0.0,
                                "payment_total": total,
                                "item_count": item_count,
                                "order_items": dc_items,
                            }
                        )
                except Exception as dce:
                    logger.warning(f"Failed loading DataCo orders: {dce}")

            # 11. Self-contained Fallbacks (Database -> Synthetic Generator)
            if not raw_events:
                logger.info("Raw CSV datasets not present; indexing directly from existing database...")
                raw_events = self._index_from_database(max_orders=max_orders)

            if not raw_events:
                logger.info("Database is empty; generating deterministic synthetic stream...")
                raw_events = self._generate_synthetic_stream(count=min(100, max_orders))

            # 12. Sort deterministically by timestamp
            raw_events.sort(key=lambda x: x["timestamp"])

            self._events_index = raw_events
            self._master_events_index = list(raw_events)
            self._current_index = 0
            self.events_processed = 0
            self.events_remaining = len(self._events_index)
            if self._events_index:
                self.current_simulated_date = self._events_index[0]["timestamp"]
            self._is_indexed = True

            duration = time.time() - start_time
            logger.info(f"Indexed {len(self._events_index)} orders in {duration:.2f}s.")
            return len(self._events_index)

        except Exception as e:
            logger.error(f"Error indexing dataset events: {e}", exc_info=True)
            return 0

    def _index_from_database(self, max_orders: int = 5000) -> list[dict[str, Any]]:
        """Indexes historical events directly from existing database records."""
        events: list[dict[str, Any]] = []
        db: Session = SessionLocal()
        try:
            # 1. Preload customers, products, sellers into cache
            for c in db.query(Customer).limit(max_orders * 2).all():
                self._customer_cache[c.customer_id] = {
                    "customer_id": c.customer_id,
                    "customer_unique_id": c.customer_unique_id or c.customer_id,
                    "customer_zip_code_prefix": c.customer_zip_code_prefix or 1000,
                    "customer_city": c.customer_city or "Sao Paulo",
                    "customer_state": c.customer_state or "SP",
                }

            for p in db.query(Product).limit(max_orders * 2).all():
                self._product_cache[p.product_id] = {
                    "product_id": p.product_id,
                    "product_category_name": p.product_category_name or "general",
                    "product_name_lenght": p.product_name_lenght,
                    "product_description_lenght": p.product_description_lenght,
                    "product_photos_qty": p.product_photos_qty,
                    "product_weight_g": p.product_weight_g,
                    "product_length_cm": p.product_length_cm,
                    "product_height_cm": p.product_height_cm,
                    "product_width_cm": p.product_width_cm,
                }

            for s in db.query(Seller).limit(max_orders).all():
                self._seller_cache[s.seller_id] = {
                    "seller_id": s.seller_id,
                    "seller_zip_code_prefix": s.seller_zip_code_prefix or 0,
                    "seller_city": s.seller_city or "unknown",
                    "seller_state": s.seller_state or "NA",
                }

            # 2. Query Olist orders
            orders = db.query(Order).limit(max_orders).all()
            order_ids = [o.order_id for o in orders]

            items_by_order: dict[str, list[dict[str, Any]]] = {}
            if order_ids:
                for i in range(0, len(order_ids), 500):
                    chunk_oids = order_ids[i : i + 500]
                    for item in db.query(OrderItem).filter(OrderItem.order_id.in_(chunk_oids)).all():
                        items_by_order.setdefault(item.order_id, []).append(
                            {
                                "product_id": item.product_id,
                                "seller_id": item.seller_id,
                                "shipping_limit_date": item.shipping_limit_date,
                                "price": float(item.price or 0.0),
                                "freight_value": float(item.freight_value or 0.0),
                            }
                        )

            pays_by_order: dict[str, list[dict[str, Any]]] = {}
            if order_ids:
                for i in range(0, len(order_ids), 500):
                    chunk_oids = order_ids[i : i + 500]
                    for pay in db.query(OrderPayment).filter(OrderPayment.order_id.in_(chunk_oids)).all():
                        pays_by_order.setdefault(pay.order_id, []).append(
                            {
                                "order_id": pay.order_id,
                                "payment_sequential": pay.payment_sequential,
                                "payment_type": pay.payment_type or "credit_card",
                                "payment_installments": pay.payment_installments or 1,
                                "payment_value": float(pay.payment_value or 0.0),
                            }
                        )

            revs_by_order: dict[str, list[dict[str, Any]]] = {}
            if order_ids:
                for i in range(0, len(order_ids), 500):
                    chunk_oids = order_ids[i : i + 500]
                    for rev in db.query(OrderReview).filter(OrderReview.order_id.in_(chunk_oids)).all():
                        revs_by_order.setdefault(rev.order_id, []).append(
                            {
                                "review_id": rev.review_id,
                                "order_id": rev.order_id,
                                "review_score": rev.review_score or 3,
                                "review_comment_title": rev.review_comment_title,
                                "review_comment_message": rev.review_comment_message,
                                "review_creation_date": rev.review_creation_date,
                                "review_answer_timestamp": rev.review_answer_timestamp,
                            }
                        )

            for o in orders:
                purch_ts = o.order_purchase_timestamp or datetime.now(UTC)
                if purch_ts.tzinfo is None:
                    purch_ts = purch_ts.replace(tzinfo=UTC)

                items = items_by_order.get(o.order_id, [])
                total_price = sum(it["price"] for it in items) if items else 0.0
                total_freight = sum(it["freight_value"] for it in items) if items else 0.0
                item_count = len(items) if items else 1

                payments = pays_by_order.get(o.order_id, [])
                reviews = revs_by_order.get(o.order_id, [])
                payment_total = (
                    sum(p["payment_value"] for p in payments) if payments else (total_price + total_freight)
                )

                cid = o.customer_id
                cust_meta = self._customer_cache.get(
                    cid,
                    {
                        "customer_id": cid,
                        "customer_unique_id": cid,
                        "customer_zip_code_prefix": 1000,
                        "customer_city": "Sao Paulo",
                        "customer_state": "SP",
                    },
                )

                is_delayed = bool(
                    o.order_delivered_customer_date
                    and o.order_estimated_delivery_date
                    and o.order_delivered_customer_date > o.order_estimated_delivery_date
                )

                events.append(
                    {
                        "source": "olist",
                        "timestamp": purch_ts,
                        "order_id": o.order_id,
                        "customer_id": cid,
                        "customer_unique_id": cust_meta.get("customer_unique_id", cid),
                        "customer_meta": cust_meta,
                        "status": o.order_status or "delivered",
                        "purchase_timestamp": purch_ts,
                        "approved_at": o.order_approved_at,
                        "delivered_carrier_date": o.order_delivered_carrier_date,
                        "delivered_customer_date": o.order_delivered_customer_date,
                        "estimated_delivery_date": o.order_estimated_delivery_date
                        or purch_ts + timedelta(days=7),
                        "is_delayed": is_delayed,
                        "merchandise_revenue": total_price,
                        "freight_value": total_freight,
                        "payment_total": payment_total,
                        "item_count": item_count,
                        "order_items": items,
                        "order_payments": payments,
                        "order_reviews": reviews,
                    }
                )

            # 3. Query DataCo orders
            dc_orders = db.query(DataCoOrder).limit(max_orders // 2).all()
            dc_oids = [d.order_id for d in dc_orders]
            dc_items_by_order: dict[int, list[dict[str, Any]]] = {}
            if dc_oids:
                for i in range(0, len(dc_oids), 500):
                    chunk_dco = dc_oids[i : i + 500]
                    for dci in (
                        db.query(DataCoOrderItem).filter(DataCoOrderItem.order_id.in_(chunk_dco)).all()
                    ):
                        dc_items_by_order.setdefault(dci.order_id, []).append(
                            {
                                "order_item_id": dci.order_item_id,
                                "product_card_id": dci.product_card_id,
                                "product_name": dci.product_name or "",
                                "category_id": dci.category_id,
                                "category_name": dci.category_name or "",
                                "department_id": dci.department_id,
                                "department_name": dci.department_name or "",
                                "product_price": float(dci.product_price or 0.0),
                                "order_item_quantity": dci.order_item_quantity or 1,
                                "sales": float(dci.sales or 0.0),
                                "order_item_discount": float(dci.order_item_discount or 0.0),
                                "order_item_discount_rate": float(dci.order_item_discount_rate or 0.0),
                                "order_item_total": float(dci.order_item_total or 0.0),
                                "order_item_profit_ratio": float(dci.order_item_profit_ratio or 0.0),
                                "order_profit_per_order": float(dci.order_profit_per_order or 0.0),
                            }
                        )

            for dco in dc_orders:
                order_ts = dco.order_date or datetime.now(UTC)
                if order_ts.tzinfo is None:
                    order_ts = order_ts.replace(tzinfo=UTC)

                dc_items = dc_items_by_order.get(dco.order_id, [])
                item_count = len(dc_items) if dc_items else 1
                total = (
                    sum(it["order_item_total"] for it in dc_items)
                    if dc_items
                    else float(dco.order_total or 50.0)
                )
                profit = (
                    sum(it["order_profit_per_order"] for it in dc_items)
                    if dc_items
                    else float(dco.order_profit or 5.0)
                )

                events.append(
                    {
                        "source": "dataco",
                        "timestamp": order_ts,
                        "order_id": dco.order_id,
                        "customer_id": dco.customer_id,
                        "customer_segment": dco.customer_segment or "Consumer",
                        "customer_city": dco.customer_city or "Caguas",
                        "customer_state": dco.customer_state or "PR",
                        "customer_country": dco.customer_country or "Puerto Rico",
                        "market": dco.market or "LATAM",
                        "order_region": dco.order_region or "Central America",
                        "order_country": dco.order_country or "Panama",
                        "order_city": dco.order_city or "Panama City",
                        "order_date": order_ts,
                        "shipping_date": dco.shipping_date,
                        "order_status": dco.order_status or "COMPLETE",
                        "shipping_mode": dco.shipping_mode or "Standard Class",
                        "delivery_status": dco.delivery_status or "Standard",
                        "late_delivery_risk": dco.late_delivery_risk or 0,
                        "days_for_shipping_real": dco.days_for_shipping_real,
                        "days_for_shipment_scheduled": dco.days_for_shipment_scheduled,
                        "payment_type": dco.payment_type or "DEBIT",
                        "order_total": total,
                        "order_profit": profit,
                        "status": dco.order_status or "COMPLETE",
                        "is_delayed": dco.late_delivery_risk == 1,
                        "merchandise_revenue": total,
                        "freight_value": 0.0,
                        "payment_total": total,
                        "item_count": item_count,
                        "order_items": dc_items,
                    }
                )

        except Exception as e:
            logger.error(f"Error indexing events from database: {e}", exc_info=True)
        finally:
            db.close()

        return events

    def _generate_synthetic_stream(self, count: int = 100) -> list[dict[str, Any]]:
        """Generates realistic chronological order events if no CSVs or DB records exist."""
        base_time = datetime(2023, 1, 1, 10, 0, 0, tzinfo=UTC)
        events = []
        cities = [
            ("Sao Paulo", "SP"),
            ("Rio de Janeiro", "RJ"),
            ("Belo Horizonte", "MG"),
            ("Curitiba", "PR"),
            ("Salvador", "BA"),
        ]
        categories = [
            "bed_bath_table",
            "health_beauty",
            "sports_leisure",
            "computers_accessories",
            "furniture_decor",
        ]

        for i in range(1, count + 1):
            ts = base_time + timedelta(hours=i * 2)
            oid = f"synth_ord_{i:05d}"
            cid = f"synth_cust_{i:04d}"
            uid = f"synth_user_{i:04d}"
            pid = f"synth_prod_{(i % 20) + 1:03d}"
            sid = f"synth_seller_{(i % 10) + 1:03d}"
            price = round(20.0 + (i * 3.5 % 300), 2)
            freight = round(10.0 + (i * 1.2 % 35), 2)
            city, state = cities[i % len(cities)]
            cat = categories[i % len(categories)]

            self._customer_cache[cid] = {
                "customer_id": cid,
                "customer_unique_id": uid,
                "customer_zip_code_prefix": 1000 + i,
                "customer_city": city,
                "customer_state": state,
            }
            self._product_cache[pid] = {
                "product_id": pid,
                "product_category_name": cat,
            }
            self._seller_cache[sid] = {
                "seller_id": sid,
                "seller_zip_code_prefix": 2000,
                "seller_city": "Sao Paulo",
                "seller_state": "SP",
            }

            events.append(
                {
                    "source": "olist",
                    "timestamp": ts,
                    "order_id": oid,
                    "customer_id": cid,
                    "customer_unique_id": uid,
                    "customer_meta": self._customer_cache[cid],
                    "status": "delivered",
                    "purchase_timestamp": ts,
                    "approved_at": ts + timedelta(minutes=15),
                    "delivered_carrier_date": ts + timedelta(days=1),
                    "delivered_customer_date": ts + timedelta(days=4),
                    "estimated_delivery_date": ts + timedelta(days=7),
                    "is_delayed": False,
                    "merchandise_revenue": price,
                    "freight_value": freight,
                    "payment_total": price + freight,
                    "item_count": 1,
                    "order_items": [
                        {
                            "product_id": pid,
                            "seller_id": sid,
                            "shipping_limit_date": ts + timedelta(days=3),
                            "price": price,
                            "freight_value": freight,
                        }
                    ],
                    "order_payments": [
                        {
                            "payment_sequential": 1,
                            "payment_type": "credit_card",
                            "payment_installments": 1,
                            "payment_value": price + freight,
                        }
                    ],
                    "order_reviews": [
                        {
                            "review_id": f"synth_rev_{i:05d}",
                            "review_score": 5 if i % 4 != 0 else 3,
                            "review_comment_title": "Good product",
                            "review_comment_message": "Delivered as described",
                            "review_creation_date": ts + timedelta(days=5),
                            "review_answer_timestamp": ts + timedelta(days=6),
                        }
                    ],
                }
            )
        return events

    def get_status(self) -> dict[str, Any]:
        """Returns the current status of the data ingestion stream."""
        if not self._is_indexed or not self._events_index:
            try:
                self.index_events_from_datasets()
            except Exception as e:
                logger.warning(f"Auto-index in get_status failed: {e}")

        total = len(self._events_index)
        remaining = max(0, total - self.events_processed)
        return {
            "status": self.status,
            "speed": self.speed,
            "simulated_date": (
                self.current_simulated_date.isoformat() if self.current_simulated_date else "N/A"
            ),
            "events_processed": self.events_processed,
            "events_remaining": remaining,
            "total_events": total,
            "last_event_timestamp": (
                self.last_event_timestamp.isoformat() if self.last_event_timestamp else "N/A"
            ),
            "orders_in_system": state_service.total_orders,
        }

    def set_speed(self, speed: int) -> int:
        """Sets ingestion speed."""
        if speed > 0:
            self.speed = speed
            logger.info(f"Ingestion speed updated to {speed} events/sec")
            publish_event("ingestion", self.get_status())
        return self.speed

    async def start(self) -> str:
        """Starts or resumes the continuous data ingestion flow."""
        if not self._is_indexed or not self._events_index:
            self.index_events_from_datasets()

        if self._current_index >= len(self._events_index) and len(self._events_index) > 0:
            return (
                "All indexed events have already been ingested. Reset ingestion to replay from the beginning."
            )

        if (
            self.status == "running"
            and self._replay_task
            and not self._replay_task.done()
            and not self._replay_task.cancelled()
        ):
            return "Ingestion flow is already running."

        self.status = "running"
        if not self._replay_task or self._replay_task.done() or self._replay_task.cancelled():
            self._replay_task = asyncio.create_task(self._run_loop())
        logger.info(f"Data ingestion stream STARTED at {self.speed}x speed.")
        publish_event("ingestion", self.get_status())
        return "Ingestion started."

    def pause(self) -> str:
        """Pauses the data ingestion flow immediately."""
        if self.status == "running":
            self.status = "paused"
            logger.info("Data ingestion stream PAUSED.")
            publish_event("ingestion", self.get_status())
            return "Ingestion paused."
        return f"Ingestion cannot be paused from state '{self.status}'."

    def resume(self) -> str:
        """Resumes the data ingestion flow from current position."""
        if self.status == "paused":
            self.status = "running"
            if not self._replay_task or self._replay_task.done() or self._replay_task.cancelled():
                self._replay_task = asyncio.create_task(self._run_loop())
            logger.info("Data ingestion stream RESUMED.")
            publish_event("ingestion", self.get_status())
            return "Ingestion resumed."
        return f"Ingestion cannot be resumed from state '{self.status}'."

    def complete_now(self) -> str:
        """
        Fast-forwards the replay straight to the end. Ingests all remaining
        indexed records in safe atomic batches, synchronizes business state,
        and lands on 'stopped'.
        """
        # Guard: if called during backend startup/lifespan, ignore so ingestion only runs via user buttons
        import inspect

        caller = inspect.currentframe().f_back
        if caller and ("main.py" in caller.f_code.co_filename or caller.f_code.co_name == "lifespan"):
            logger.info(
                "Startup complete_now skipped: data ingestion operates strictly through button controls."
            )
            return "Startup sync skipped; streaming operates through button only."

        self.status = "stopped"
        if self._replay_task and not self._replay_task.done():
            self._replay_task.cancel()
        self._replay_task = None

        if not self._is_indexed or not self._events_index:
            self.index_events_from_datasets()

        remaining = len(self._events_index) - self._current_index
        ingested = self.step(count=remaining) if remaining > 0 else 0
        logger.info(f"Data ingestion stream FAST-FORWARDED: {ingested} remaining record(s) ingested.")
        publish_event("ingestion", self.get_status())
        return f"Fast-forwarded — ingested the remaining {ingested} record(s)."

    def stop(self) -> str:
        """Stops the data ingestion flow."""
        self.status = "stopped"
        if self._replay_task and not self._replay_task.done():
            self._replay_task.cancel()
        self._replay_task = None
        logger.info("Data ingestion stream STOPPED.")
        publish_event("ingestion", self.get_status())
        return "Ingestion stopped."

    def reset(self, clear_db: bool = True) -> str:
        """Resets the ingestion position, empties active order tables, and resets agent memory."""
        self.stop()
        self._current_index = 0
        self.events_processed = 0
        if not self._events_index:
            self.index_events_from_datasets()
        self.events_remaining = len(self._events_index)
        if self._events_index:
            self.current_simulated_date = self._events_index[0]["timestamp"]
        self.last_event_timestamp = None

        state_service.reset_state()

        db_cleared = True
        if clear_db:
            db = SessionLocal()
            try:
                # Clear child tables first to respect foreign key constraints
                db.query(OrderPayment).delete()
                db.query(OrderReview).delete()
                db.query(OrderItem).delete()
                db.query(Order).delete()
                db.query(DataCoOrderItem).delete()
                db.query(DataCoOrder).delete()
                db.commit()
                logger.info("Active order records cleared from database for reset.")
            except Exception as e:
                db.rollback()
                db_cleared = False
                logger.warning(f"Error resetting database tables: {e}")
            finally:
                db.close()

        from app.agents.orders import orders_agent

        orders_agent.reset()

        logger.info("Data ingestion engine and Orders Agent RESET to initial state.")
        publish_event("ingestion", self.get_status())
        if clear_db and not db_cleared:
            return "Ingestion position and agent memory reset, but clearing the database failed — see server logs. No order data was cleared."
        return "Ingestion and agent reset." + (" Database cleared." if clear_db else "")

    def step(self, count: int = 50) -> int:
        """Synchronously ingests exactly 'count' order records into the system."""
        if not self._is_indexed or not self._events_index:
            self.index_events_from_datasets()

        actual_ingested = self._ingest_batch(count)
        logger.info(
            f"Stepped {actual_ingested} records into the system. Total in system: {self.events_processed}"
        )
        return actual_ingested

    def _ingest_batch(self, batch_size: int) -> int:
        """
        Internal worker to ingest records into database and publish events.
        Thread-safe wrapper acquiring _ingest_lock before performing DB operations.
        """
        with self._ingest_lock:
            return self._ingest_batch_unlocked(batch_size)

    def _ingest_batch_unlocked(self, batch_size: int) -> int:
        """
        Core ingestion worker. Processes in chunks of at most 100 items per database transaction
        to respect SQLite variable limits and ensure atomic commits.
        """
        if self._current_index >= len(self._events_index):
            return 0

        end_index = min(len(self._events_index), self._current_index + batch_size)
        total_to_ingest = end_index - self._current_index
        if total_to_ingest <= 0:
            return 0

        CHUNK_SIZE = 100
        total_ingested = 0

        for chunk_start in range(self._current_index, end_index, CHUNK_SIZE):
            chunk_end = min(end_index, chunk_start + CHUNK_SIZE)
            sub_batch = self._events_index[chunk_start:chunk_end]

            db: Session = SessionLocal()
            try:
                olist_records: list[dict[str, Any]] = []
                dataco_records: list[dict[str, Any]] = []
                olist_items_records: list[dict[str, Any]] = []
                dataco_items_records: list[dict[str, Any]] = []
                olist_payments_records: list[dict[str, Any]] = []
                olist_reviews_records: list[dict[str, Any]] = []

                for item in sub_batch:
                    ts = item["timestamp"]
                    self.current_simulated_date = ts
                    self.last_event_timestamp = ts

                    # 1. Publish Event to Event Bus and State Service
                    evt = OperationalEvent(
                        event_type="ORDER_CREATED",
                        event_timestamp=ts,
                        source_record_id=str(item["order_id"]),
                        payload=item,
                    )
                    event_bus.publish(evt)

                    # 2. Prepare Olist DB records
                    if item["source"] == "olist":
                        olist_records.append(
                            {
                                "order_id": item["order_id"],
                                "customer_id": item["customer_id"],
                                "order_status": item["status"],
                                "order_purchase_timestamp": item["purchase_timestamp"],
                                "order_approved_at": item.get("approved_at"),
                                "order_delivered_carrier_date": item.get("delivered_carrier_date"),
                                "order_delivered_customer_date": item.get("delivered_customer_date"),
                                "order_estimated_delivery_date": item["estimated_delivery_date"],
                                "_customer_meta": item.get("customer_meta"),
                            }
                        )

                        # Order items
                        for idx, order_item in enumerate(item.get("order_items", [])):
                            olist_items_records.append(
                                {
                                    "order_id": item["order_id"],
                                    "order_item_id": idx + 1,
                                    "product_id": order_item["product_id"],
                                    "seller_id": order_item["seller_id"],
                                    "shipping_limit_date": order_item.get("shipping_limit_date") or ts,
                                    "price": order_item.get("price", 0.0),
                                    "freight_value": order_item.get("freight_value", 0.0),
                                }
                            )

                        # Order payments
                        for payment in item.get("order_payments", []):
                            olist_payments_records.append(
                                {
                                    "order_id": item["order_id"],
                                    "payment_sequential": payment.get("payment_sequential", 1),
                                    "payment_type": payment.get("payment_type", "credit_card"),
                                    "payment_installments": payment.get("payment_installments", 1),
                                    "payment_value": payment.get("payment_value", 0.0),
                                }
                            )

                        # Order reviews
                        for review in item.get("order_reviews", []):
                            olist_reviews_records.append(
                                {
                                    "review_id": review["review_id"],
                                    "order_id": item["order_id"],
                                    "review_score": review.get("review_score", 3),
                                    "review_comment_title": review.get("review_comment_title"),
                                    "review_comment_message": review.get("review_comment_message"),
                                    "review_creation_date": review.get("review_creation_date") or ts,
                                    "review_answer_timestamp": review.get("review_answer_timestamp"),
                                }
                            )

                    # 3. Prepare DataCo DB records
                    elif item["source"] == "dataco":
                        dataco_records.append(
                            {
                                "order_id": item["order_id"],
                                "customer_id": item["customer_id"],
                                "customer_segment": item.get("customer_segment"),
                                "customer_city": item.get("customer_city"),
                                "customer_state": item.get("customer_state"),
                                "customer_country": item.get("customer_country"),
                                "market": item.get("market"),
                                "order_region": item.get("order_region"),
                                "order_country": item.get("order_country"),
                                "order_city": item.get("order_city"),
                                "order_date": item["order_date"],
                                "shipping_date": item.get("shipping_date"),
                                "order_status": item["order_status"],
                                "shipping_mode": item.get("shipping_mode", "Standard Class"),
                                "delivery_status": item.get("delivery_status", "Standard"),
                                "late_delivery_risk": item.get("late_delivery_risk", 0),
                                "days_for_shipping_real": item.get("days_for_shipping_real"),
                                "days_for_shipment_scheduled": item.get("days_for_shipment_scheduled"),
                                "payment_type": item.get("payment_type"),
                                "order_total": item.get("order_total", 0.0),
                                "order_profit": item.get("order_profit", 0.0),
                                "source": "dataco",
                            }
                        )

                        # DataCo order items
                        for order_item in item.get("order_items", []):
                            dataco_items_records.append(
                                {
                                    "order_item_id": order_item["order_item_id"],
                                    "order_id": item["order_id"],
                                    "product_card_id": order_item["product_card_id"],
                                    "product_name": order_item.get("product_name", ""),
                                    "category_id": order_item.get("category_id"),
                                    "category_name": order_item.get("category_name", ""),
                                    "department_id": order_item.get("department_id"),
                                    "department_name": order_item.get("department_name", ""),
                                    "product_price": order_item.get("product_price", 0.0),
                                    "order_item_quantity": order_item.get("order_item_quantity", 1),
                                    "sales": order_item.get("sales", 0.0),
                                    "order_item_discount": order_item.get("order_item_discount", 0.0),
                                    "order_item_discount_rate": order_item.get(
                                        "order_item_discount_rate", 0.0
                                    ),
                                    "order_item_total": order_item.get("order_item_total", 0.0),
                                    "order_item_profit_ratio": order_item.get("order_item_profit_ratio", 0.0),
                                    "order_profit_per_order": order_item.get("order_profit_per_order", 0.0),
                                }
                            )

                # --- Database Commit for Chunk ---

                # Olist Customers & Orders
                if olist_records:
                    cids_in_chunk = list({r["customer_id"] for r in olist_records})
                    existing_cids = {
                        r[0]
                        for r in db.query(Customer.customer_id)
                        .filter(Customer.customer_id.in_(cids_in_chunk))
                        .all()
                    }
                    new_custs = []
                    seen_cids = set(existing_cids)
                    for rec in olist_records:
                        cid = rec["customer_id"]
                        if cid not in seen_cids:
                            seen_cids.add(cid)
                            meta = rec.get("_customer_meta") or self._customer_cache.get(cid, {})
                            new_custs.append(
                                {
                                    "customer_id": cid,
                                    "customer_unique_id": meta.get("customer_unique_id", cid),
                                    "customer_zip_code_prefix": meta.get("customer_zip_code_prefix", 1000),
                                    "customer_city": meta.get("customer_city", "Sao Paulo"),
                                    "customer_state": meta.get("customer_state", "SP"),
                                }
                            )
                    if new_custs:
                        db.bulk_insert_mappings(Customer, new_custs)

                    olist_ids = [r["order_id"] for r in olist_records]
                    existing_olist_ids = {
                        r[0] for r in db.query(Order.order_id).filter(Order.order_id.in_(olist_ids)).all()
                    }
                    seen_olist_ids = set(existing_olist_ids)
                    to_insert_olist = []
                    for r in olist_records:
                        if r["order_id"] not in seen_olist_ids:
                            seen_olist_ids.add(r["order_id"])
                            # Strip internal key before inserting
                            row_data = {k: v for k, v in r.items() if not k.startswith("_")}
                            to_insert_olist.append(row_data)
                    if to_insert_olist:
                        db.bulk_insert_mappings(Order, to_insert_olist)

                # Olist Products, Sellers, Order Items
                if olist_items_records:
                    pids_in_chunk = list({r["product_id"] for r in olist_items_records})
                    sids_in_chunk = list({r["seller_id"] for r in olist_items_records})

                    existing_prods = {
                        r[0]
                        for r in db.query(Product.product_id)
                        .filter(Product.product_id.in_(pids_in_chunk))
                        .all()
                    }
                    existing_sellers = {
                        r[0]
                        for r in db.query(Seller.seller_id).filter(Seller.seller_id.in_(sids_in_chunk)).all()
                    }

                    missing_prods = set(pids_in_chunk) - existing_prods
                    missing_sellers = set(sids_in_chunk) - existing_sellers

                    if missing_prods:
                        new_prods = []
                        for pid in missing_prods:
                            pmeta = self._product_cache.get(pid, {})
                            new_prods.append(
                                {
                                    "product_id": pid,
                                    "product_category_name": pmeta.get("product_category_name", "general"),
                                    "product_name_lenght": pmeta.get("product_name_lenght"),
                                    "product_description_lenght": pmeta.get("product_description_lenght"),
                                    "product_photos_qty": pmeta.get("product_photos_qty"),
                                    "product_weight_g": pmeta.get("product_weight_g"),
                                    "product_length_cm": pmeta.get("product_length_cm"),
                                    "product_height_cm": pmeta.get("product_height_cm"),
                                    "product_width_cm": pmeta.get("product_width_cm"),
                                }
                            )
                        db.bulk_insert_mappings(Product, new_prods)

                    if missing_sellers:
                        new_sellers = []
                        for sid in missing_sellers:
                            smeta = self._seller_cache.get(sid, {})
                            new_sellers.append(
                                {
                                    "seller_id": sid,
                                    "seller_zip_code_prefix": smeta.get("seller_zip_code_prefix", 0),
                                    "seller_city": smeta.get("seller_city", "unknown"),
                                    "seller_state": smeta.get("seller_state", "NA"),
                                }
                            )
                        db.bulk_insert_mappings(Seller, new_sellers)

                    # Deduplicate items
                    item_order_ids = list({r["order_id"] for r in olist_items_records})
                    existing_items = {
                        (r[0], r[1])
                        for r in db.query(OrderItem.order_id, OrderItem.order_item_id)
                        .filter(OrderItem.order_id.in_(item_order_ids))
                        .all()
                    }
                    seen_items = set(existing_items)
                    to_insert_items = []
                    for r in olist_items_records:
                        key = (r["order_id"], r["order_item_id"])
                        if key not in seen_items:
                            seen_items.add(key)
                            to_insert_items.append(r)
                    if to_insert_items:
                        db.bulk_insert_mappings(OrderItem, to_insert_items)

                # Olist Payments
                if olist_payments_records:
                    pay_order_ids = list({r["order_id"] for r in olist_payments_records})
                    existing_pays = {
                        (r[0], r[1])
                        for r in db.query(OrderPayment.order_id, OrderPayment.payment_sequential)
                        .filter(OrderPayment.order_id.in_(pay_order_ids))
                        .all()
                    }
                    seen_pays = set(existing_pays)
                    to_insert_pays = []
                    for r in olist_payments_records:
                        key = (r["order_id"], r["payment_sequential"])
                        if key not in seen_pays:
                            seen_pays.add(key)
                            to_insert_pays.append(r)
                    if to_insert_pays:
                        db.bulk_insert_mappings(OrderPayment, to_insert_pays)

                # Olist Reviews
                if olist_reviews_records:
                    rev_order_ids = list({r["order_id"] for r in olist_reviews_records})
                    existing_revs = {
                        (r[0], r[1])
                        for r in db.query(OrderReview.review_id, OrderReview.order_id)
                        .filter(OrderReview.order_id.in_(rev_order_ids))
                        .all()
                    }
                    seen_revs = set(existing_revs)
                    to_insert_revs = []
                    for r in olist_reviews_records:
                        key = (r["review_id"], r["order_id"])
                        if key not in seen_revs:
                            seen_revs.add(key)
                            to_insert_revs.append(r)
                    if to_insert_revs:
                        db.bulk_insert_mappings(OrderReview, to_insert_revs)

                # DataCo Orders
                if dataco_records:
                    dc_ids = [r["order_id"] for r in dataco_records]
                    existing_dc_ids = {
                        r[0]
                        for r in db.query(DataCoOrder.order_id).filter(DataCoOrder.order_id.in_(dc_ids)).all()
                    }
                    seen_dc_ids = set(existing_dc_ids)
                    to_insert_dc = []
                    for r in dataco_records:
                        if r["order_id"] not in seen_dc_ids:
                            seen_dc_ids.add(r["order_id"])
                            to_insert_dc.append(r)
                    if to_insert_dc:
                        db.bulk_insert_mappings(DataCoOrder, to_insert_dc)

                # DataCo Order Items
                if dataco_items_records:
                    item_ids = [r["order_item_id"] for r in dataco_items_records]
                    existing_dc_items = {
                        r[0]
                        for r in db.query(DataCoOrderItem.order_item_id)
                        .filter(DataCoOrderItem.order_item_id.in_(item_ids))
                        .all()
                    }
                    seen_dc_items = set(existing_dc_items)
                    to_insert_dc_items = []
                    for r in dataco_items_records:
                        if r["order_item_id"] not in seen_dc_items:
                            seen_dc_items.add(r["order_item_id"])
                            to_insert_dc_items.append(r)
                    if to_insert_dc_items:
                        db.bulk_insert_mappings(DataCoOrderItem, to_insert_dc_items)

                db.commit()

                chunk_count = len(sub_batch)
                self._current_index += chunk_count
                self.events_processed += chunk_count
                self.events_remaining = len(self._events_index) - self.events_processed
                total_ingested += chunk_count

            except Exception as e:
                db.rollback()
                logger.error(f"Error writing ingested chunk to database: {e}", exc_info=True)
                break
            finally:
                db.close()

        return total_ingested

    async def _run_loop(self) -> None:
        """Asynchronous worker loop continuously streaming events when running."""
        logger.info("Entering data ingestion stream loop...")
        try:
            while self.status == "running" and self._current_index < len(self._events_index):
                # For smooth, visible streaming at speeds < 10, ingest 1 record per tick.
                # At higher speeds (50, 200), chunk proportionally.
                chunk_size = max(1, min(50, self.speed // 10)) if self.speed >= 10 else 1
                ingested = await asyncio.to_thread(self._ingest_batch, chunk_size)

                if ingested == 0:
                    self.status = "stopped"
                    break

                publish_event("ingestion", self.get_status())
                interval = max(0.04, float(chunk_size) / float(self.speed))
                await asyncio.sleep(interval)

            if self._current_index >= len(self._events_index):
                self.status = "stopped"
                logger.info("All indexed order events ingested.")
                publish_event("ingestion", self.get_status())
        except asyncio.CancelledError:
            logger.info("Ingestion stream task cancelled.")
        except Exception as e:
            logger.error(f"Error in data ingestion loop: {e}", exc_info=True)
            self.status = "stopped"
            publish_event("ingestion", self.get_status())


replay_engine = ReplayEngine()
