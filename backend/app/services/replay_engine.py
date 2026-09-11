import asyncio
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
import pandas as pd
from sqlalchemy.orm import Session

from app.database.session import SessionLocal, init_db
from app.models.olist import Customer, Order, OrderItem, Product
from app.models.dataco import DataCoOrder, DataCoOrderItem
from app.services.event_bus import OperationalEvent, event_bus
from app.services.state_service import state_service

logger = logging.getLogger(__name__)

# Nexus data directory from environment variable with project-relative fallback
def _get_nexus_data_dirs() -> List[str]:
    env_dir = os.environ.get("NEXUS_DATA_DIR")
    if env_dir:
        return [env_dir]
    # Project-relative fallbacks
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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


def find_dataset(filename: str) -> Optional[str]:
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

    SUPPORTED_SPEEDS = [1, 5, 10, 50, 60, 200, 300]

    def __init__(self):
        self.status: str = "stopped"  # "stopped", "running", "paused"
        self.speed: int = 50          # events per second base
        self.events_processed: int = 0
        self.events_remaining: int = 0
        self.current_simulated_date: Optional[datetime] = None
        self.last_event_timestamp: Optional[datetime] = None

        self._events_index: List[Dict[str, Any]] = []
        self._current_index: int = 0
        self._replay_task: Optional[asyncio.Task] = None
        self._is_indexed: bool = False

    def index_events_from_datasets(self, max_orders: int = 5000) -> int:
        """Loads and sorts historical orders chronologically from Nexus raw CSVs."""
        logger.info(f"Indexing Nexus order records (limit: {max_orders})...")
        start_time = time.time()
        raw_events: List[Dict[str, Any]] = []

        try:
            # 1. Load Olist Order Items first to get price/freight data
            items_path = find_dataset("olist_order_items_dataset.csv")
            olist_items_by_order: Dict[str, List[Dict]] = {}
            if items_path:
                df_items = pd.read_csv(items_path, nrows=max_orders * 3)
                df_items["shipping_limit_date"] = pd.to_datetime(df_items["shipping_limit_date"], errors="coerce")
                for _, row in df_items.iterrows():
                    oid = str(row["order_id"])
                    if oid not in olist_items_by_order:
                        olist_items_by_order[oid] = []
                    olist_items_by_order[oid].append({
                        "product_id": str(row["product_id"]),
                        "seller_id": str(row["seller_id"]),
                        "shipping_limit_date": row["shipping_limit_date"].to_pydatetime() if pd.notna(row["shipping_limit_date"]) else None,
                        "price": float(row["price"]) if pd.notna(row.get("price")) else 0.0,
                        "freight_value": float(row["freight_value"]) if pd.notna(row.get("freight_value")) else 0.0,
                    })

            # 2. Load Olist Orders
            orders_path = find_dataset("olist_orders_dataset.csv")
            if orders_path:
                df_orders = pd.read_csv(orders_path, nrows=max_orders)
                for col in [
                    "order_purchase_timestamp",
                    "order_approved_at",
                    "order_delivered_carrier_date",
                    "order_delivered_customer_date",
                    "order_estimated_delivery_date",
                ]:
                    df_orders[col] = pd.to_datetime(df_orders[col], errors="coerce")

                seen_olist_ids = set()
                for _, row in df_orders.iterrows():
                    purch_dt = row["order_purchase_timestamp"]
                    if pd.isna(purch_dt):
                        continue
                    purch_ts = purch_dt.to_pydatetime()
                    if purch_ts.tzinfo is None:
                        purch_ts = purch_ts.replace(tzinfo=timezone.utc)

                    oid = str(row["order_id"])
                    if oid in seen_olist_ids:
                        continue
                    seen_olist_ids.add(oid)

                    cid = str(row["customer_id"])
                    status = str(row.get("order_status", "delivered"))

                    est_dt = row["order_estimated_delivery_date"]
                    deliv_dt = row["order_delivered_customer_date"]
                    is_delayed = bool(pd.notna(deliv_dt) and pd.notna(est_dt) and deliv_dt > est_dt)

                    # Get actual price/freight from order items
                    items = olist_items_by_order.get(oid, [])
                    total_price = sum(item["price"] for item in items) if items else 0.0
                    total_freight = sum(item["freight_value"] for item in items) if items else 0.0
                    item_count = len(items) if items else 1

                    raw_events.append({
                        "source": "olist",
                        "timestamp": purch_ts,
                        "order_id": oid,
                        "customer_id": cid,
                        "status": status,
                        "purchase_timestamp": purch_ts,
                        "approved_at": row["order_approved_at"].to_pydatetime() if pd.notna(row["order_approved_at"]) else None,
                        "delivered_carrier_date": row["order_delivered_carrier_date"].to_pydatetime() if pd.notna(row["order_delivered_carrier_date"]) else None,
                        "delivered_customer_date": deliv_dt.to_pydatetime() if pd.notna(deliv_dt) else None,
                        "estimated_delivery_date": est_dt.to_pydatetime() if pd.notna(est_dt) else purch_ts + timedelta(days=7),
                        "is_delayed": is_delayed,
                        "merchandise_revenue": total_price,
                        "freight_value": total_freight,
                        "item_count": item_count,
                        "order_items": items,  # Store items for later ingestion
                    })

            # 3. Load DataCo Order Items first
            dc_path = find_dataset("DataCoSupplyChainDataset.csv")
            dc_items_by_order: Dict[int, List[Dict]] = {}
            if dc_path:
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
                df_dc_items = pd.read_csv(dc_path, usecols=dc_item_cols, nrows=max_orders * 2, encoding="latin1")
                for _, row in df_dc_items.iterrows():
                    oid = int(row["Order Id"])
                    if oid not in dc_items_by_order:
                        dc_items_by_order[oid] = []
                    dc_items_by_order[oid].append({
                        "order_item_id": int(row["Order Item Id"]),
                        "product_card_id": int(row["Product Card Id"]),
                        "product_name": str(row.get("Product Name", "")),
                        "category_id": int(row["Category Id"]) if pd.notna(row.get("Category Id")) else None,
                        "category_name": str(row.get("Category Name", "")),
                        "department_id": int(row["Department Id"]) if pd.notna(row.get("Department Id")) else None,
                        "department_name": str(row.get("Department Name", "")),
                        "product_price": float(row["Product Price"]) if pd.notna(row.get("Product Price")) else 0.0,
                        "order_item_quantity": int(row["Order Item Quantity"]) if pd.notna(row.get("Order Item Quantity")) else 1,
                        "sales": float(row["Sales"]) if pd.notna(row.get("Sales")) else 0.0,
                        "order_item_discount": float(row["Order Item Discount"]) if pd.notna(row.get("Order Item Discount")) else 0.0,
                        "order_item_discount_rate": float(row["Order Item Discount Rate"]) if pd.notna(row.get("Order Item Discount Rate")) else 0.0,
                        "order_item_total": float(row["Order Item Total"]) if pd.notna(row.get("Order Item Total")) else 0.0,
                        "order_item_profit_ratio": float(row["Order Item Profit Ratio"]) if pd.notna(row.get("Order Item Profit Ratio")) else 0.0,
                        "order_profit_per_order": float(row["Order Profit Per Order"]) if pd.notna(row.get("Order Profit Per Order")) else 0.0,
                    })

            # 4. Load DataCo Orders (using the same CSV, different columns)
            if dc_path:
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
                df_dc["order date (DateOrders)"] = pd.to_datetime(df_dc["order date (DateOrders)"], errors="coerce")
                df_dc["shipping date (DateOrders)"] = pd.to_datetime(df_dc["shipping date (DateOrders)"], errors="coerce")

                seen_dc_ids = set()
                for _, row in df_dc.iterrows():
                    odt = row["order date (DateOrders)"]
                    if pd.isna(odt):
                        continue
                    order_ts = odt.to_pydatetime()
                    if order_ts.tzinfo is None:
                        order_ts = order_ts.replace(tzinfo=timezone.utc)

                    oid = int(row["Order Id"])
                    if oid in seen_dc_ids:
                        continue
                    seen_dc_ids.add(oid)

                    cid = int(row["Order Customer Id"]) if pd.notna(row.get("Order Customer Id")) else 0
                    status = str(row.get("Order Status") or "COMPLETE").strip()
                    late_risk = int(row["Late_delivery_risk"]) if pd.notna(row.get("Late_delivery_risk")) else 0
                    total = float(row["Order Item Total"]) if pd.notna(row.get("Order Item Total")) else 50.0
                    profit = float(row["Order Profit Per Order"]) if pd.notna(row.get("Order Profit Per Order")) else 5.0

                    ship_dt = row["shipping date (DateOrders)"]

                    # Get actual items for this order
                    dc_items = dc_items_by_order.get(oid, [])
                    item_count = len(dc_items) if dc_items else 1

                    raw_events.append({
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
                        "days_for_shipping_real": float(row["Days for shipping (real)"]) if pd.notna(row.get("Days for shipping (real)")) else None,
                        "days_for_shipment_scheduled": float(row["Days for shipment (scheduled)"]) if pd.notna(row.get("Days for shipment (scheduled)")) else None,
                        "payment_type": str(row.get("Type") or "DEBIT"),
                        "order_total": total,
                        "order_profit": profit,
                        "status": status,
                        "is_delayed": late_risk == 1,
                        "merchandise_revenue": total,
                        "freight_value": 0.0,  # DataCo doesn't have separate freight
                        "item_count": item_count,
                        "order_items": dc_items,  # Store items for later ingestion
                    })

            # Sort deterministically by timestamp
            raw_events.sort(key=lambda x: x["timestamp"])

            self._events_index = raw_events
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

    def get_status(self) -> Dict[str, Any]:
        """Returns the current status of the data ingestion stream."""
        return {
            "status": self.status,
            "speed": self.speed,
            "simulated_date": self.current_simulated_date.isoformat() if self.current_simulated_date else "N/A",
            "events_processed": self.events_processed,
            "events_remaining": self.events_remaining,
            "total_events": len(self._events_index),
            "last_event_timestamp": self.last_event_timestamp.isoformat() if self.last_event_timestamp else "N/A",
            "orders_in_system": state_service.total_orders,
        }

    def set_speed(self, speed: int) -> int:
        """Sets ingestion speed."""
        if speed > 0:
            self.speed = speed
            logger.info(f"Ingestion speed updated to {speed} events/sec")
        return self.speed

    async def start(self) -> str:
        """Starts or resumes the continuous data ingestion flow."""
        if not self._is_indexed or not self._events_index:
            self.index_events_from_datasets()

        if self.status == "running":
            return "Ingestion flow is already running."

        self.status = "running"
        if not self._replay_task or self._replay_task.done():
            self._replay_task = asyncio.create_task(self._run_loop())
        logger.info(f"Data ingestion stream STARTED at {self.speed}x speed.")
        return "Ingestion started."

    def pause(self) -> str:
        """Pauses the data ingestion flow immediately."""
        if self.status == "running":
            self.status = "paused"
            logger.info("Data ingestion stream PAUSED.")
            return "Ingestion paused."
        return f"Ingestion cannot be paused from state '{self.status}'."

    def resume(self) -> str:
        """Resumes the data ingestion flow from current position."""
        if self.status == "paused":
            self.status = "running"
            if not self._replay_task or self._replay_task.done():
                self._replay_task = asyncio.create_task(self._run_loop())
            logger.info("Data ingestion stream RESUMED.")
            return "Ingestion resumed."
        return f"Ingestion cannot be resumed from state '{self.status}'."

    def stop(self) -> str:
        """Stops the data ingestion flow."""
        self.status = "stopped"
        if self._replay_task and not self._replay_task.done():
            self._replay_task.cancel()
        logger.info("Data ingestion stream STOPPED.")
        return "Ingestion stopped."

    def reset(self, clear_db: bool = True) -> str:
        """Resets the ingestion position, empties active order tables, and resets agent memory."""
        self.stop()
        self._current_index = 0
        self.events_processed = 0
        self.events_remaining = len(self._events_index)
        if self._events_index:
            self.current_simulated_date = self._events_index[0]["timestamp"]
        self.last_event_timestamp = None

        state_service.reset_state()

        if clear_db:
            db = SessionLocal()
            try:
                db.query(OrderItem).delete()
                db.query(Order).delete()
                db.query(DataCoOrderItem).delete()
                db.query(DataCoOrder).delete()
                db.commit()
                logger.info("Active order records cleared from database for reset.")
            except Exception as e:
                db.rollback()
                logger.warning(f"Error resetting database tables: {e}")
            finally:
                db.close()

        from app.agents.orders import orders_agent
        orders_agent.reset()

        logger.info("Data ingestion engine and Orders Agent RESET to initial state.")
        return "Ingestion and agent reset."

    def step(self, count: int = 50) -> int:
        """Synchronously ingests exactly 'count' order records into the system."""
        if not self._is_indexed or not self._events_index:
            self.index_events_from_datasets()

        actual_ingested = self._ingest_batch(count)
        logger.info(f"Stepped {actual_ingested} records into the system. Total in system: {self.events_processed}")
        return actual_ingested

    def _ingest_batch(self, batch_size: int) -> int:
        """Internal worker to ingest a batch into database and publish events."""
        if self._current_index >= len(self._events_index):
            return 0

        end_index = min(len(self._events_index), self._current_index + batch_size)
        batch = self._events_index[self._current_index : end_index]

        db: Session = SessionLocal()
        try:
            olist_records = []
            dataco_records = []
            olist_items_records = []
            dataco_items_records = []

            for item in batch:
                ts = item["timestamp"]
                self.current_simulated_date = ts
                self.last_event_timestamp = ts

                # 1. Publish Event
                evt = OperationalEvent(
                    event_type="ORDER_CREATED",
                    event_timestamp=ts,
                    source_record_id=str(item["order_id"]),
                    payload=item,
                )
                event_bus.publish(evt)

                # 2. Prepare DB record
                if item["source"] == "olist":
                    olist_records.append({
                        "order_id": item["order_id"],
                        "customer_id": item["customer_id"],
                        "order_status": item["status"],
                        "order_purchase_timestamp": item["purchase_timestamp"],
                        "order_approved_at": item.get("approved_at"),
                        "order_delivered_carrier_date": item.get("delivered_carrier_date"),
                        "order_delivered_customer_date": item.get("delivered_customer_date"),
                        "order_estimated_delivery_date": item["estimated_delivery_date"],
                    })

                    # Prepare order items
                    for idx, order_item in enumerate(item.get("order_items", [])):
                        olist_items_records.append({
                            "order_id": item["order_id"],
                            "order_item_id": idx + 1,
                            "product_id": order_item["product_id"],
                            "seller_id": order_item["seller_id"],
                            "shipping_limit_date": order_item["shipping_limit_date"] or ts,
                            "price": order_item["price"],
                            "freight_value": order_item["freight_value"],
                        })

                elif item["source"] == "dataco":
                    dataco_records.append({
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
                    })

                    # Prepare DataCo order items
                    for order_item in item.get("order_items", []):
                        dataco_items_records.append({
                            "order_item_id": order_item["order_item_id"],
                            "order_id": item["order_id"],
                            "product_card_id": order_item["product_card_id"],
                            "product_name": order_item["product_name"],
                            "category_id": order_item["category_id"],
                            "category_name": order_item["category_name"],
                            "department_id": order_item["department_id"],
                            "department_name": order_item["department_name"],
                            "product_price": order_item["product_price"],
                            "order_item_quantity": order_item["order_item_quantity"],
                            "sales": order_item["sales"],
                            "order_item_discount": order_item["order_item_discount"],
                            "order_item_discount_rate": order_item["order_item_discount_rate"],
                            "order_item_total": order_item["order_item_total"],
                            "order_item_profit_ratio": order_item["order_item_profit_ratio"],
                            "order_profit_per_order": order_item["order_profit_per_order"],
                        })

            # Commit batch to database avoiding duplicates
            if olist_records:
                existing_cids = set(r[0] for r in db.query(Customer.customer_id).all())
                new_custs = []
                for rec in olist_records:
                    cid = rec["customer_id"]
                    if cid not in existing_cids:
                        existing_cids.add(cid)
                        new_custs.append({
                            "customer_id": cid,
                            "customer_unique_id": cid,
                            "customer_zip_code_prefix": 1000,
                            "customer_city": "Sao Paulo",
                            "customer_state": "SP",
                        })
                if new_custs:
                    db.bulk_insert_mappings(Customer, new_custs)

                olist_ids = [r["order_id"] for r in olist_records]
                existing_olist_ids = set(r[0] for r in db.query(Order.order_id).filter(Order.order_id.in_(olist_ids)).all())
                to_insert_olist = [r for r in olist_records if r["order_id"] not in existing_olist_ids]
                if to_insert_olist:
                    db.bulk_insert_mappings(Order, to_insert_olist)

            if olist_items_records:
                # Insert order items (composite key: order_id, order_item_id)
                existing_items = set((r[0], r[1]) for r in db.query(OrderItem.order_id, OrderItem.order_item_id).all())
                to_insert_items = [r for r in olist_items_records if (r["order_id"], r["order_item_id"]) not in existing_items]
                if to_insert_items:
                    db.bulk_insert_mappings(OrderItem, to_insert_items)

            if dataco_records:
                dc_ids = [r["order_id"] for r in dataco_records]
                existing_dc_ids = set(r[0] for r in db.query(DataCoOrder.order_id).filter(DataCoOrder.order_id.in_(dc_ids)).all())
                to_insert_dc = [r for r in dataco_records if r["order_id"] not in existing_dc_ids]
                if to_insert_dc:
                    db.bulk_insert_mappings(DataCoOrder, to_insert_dc)

            if dataco_items_records:
                existing_dc_items = set((r[0], r[1]) for r in db.query(DataCoOrderItem.order_id, DataCoOrderItem.order_item_id).all())
                to_insert_dc_items = [r for r in dataco_items_records if (r["order_id"], r["order_item_id"]) not in existing_dc_items]
                if to_insert_dc_items:
                    db.bulk_insert_mappings(DataCoOrderItem, to_insert_dc_items)

            db.commit()

            count = len(batch)
            self._current_index += count
            self.events_processed += count
            self.events_remaining = len(self._events_index) - self.events_processed
            return count

        except Exception as e:
            db.rollback()
            logger.error(f"Error writing ingested batch to database: {e}", exc_info=True)
            return 0
        finally:
            db.close()

    async def _run_loop(self) -> None:
        """Asynchronous worker loop continuously streaming events when running."""
        logger.info("Entering data ingestion stream loop...")
        try:
            while self.status == "running" and self._current_index < len(self._events_index):
                # Calculate chunk size based on speed
                # If speed=50, chunk=5 every 0.1s
                chunk_size = max(1, min(50, self.speed // 10))
                ingested = self._ingest_batch(chunk_size)

                if ingested == 0:
                    break

                # Sleep interval
                interval = max(0.02, float(chunk_size) / float(self.speed))
                await asyncio.sleep(interval)

            if self._current_index >= len(self._events_index):
                self.status = "stopped"
                logger.info("All indexed order events ingested.")
        except asyncio.CancelledError:
            logger.info("Ingestion stream task cancelled.")
        except Exception as e:
            logger.error(f"Error in data ingestion loop: {e}", exc_info=True)
            self.status = "stopped"


replay_engine = ReplayEngine()
