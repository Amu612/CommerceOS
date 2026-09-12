# CommerceOS — Database Schema

PostgreSQL 15. Schema is owned by Alembic (`backend/alembic/versions/`).
Generate the ERD with `make -C backend erd` → `docs/img/erd.svg` (task D8).

Two logical groups:

- **Warehouse** (immutable-ish historical facts, loaded by `scripts/build_warehouse.py`)
- **Operational** (mutable platform state, written by the app/agents/orchestrator)

---

## Warehouse — Olist marketplace

| Table                               | Grain                      | Key columns                                                                                                                                                                                              | Notes                                                                                                                 |
| ----------------------------------- | -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `customers`                         | one row per order-customer | `customer_id` PK, `customer_unique_id`, `customer_zip_code_prefix`, `customer_city`, `customer_state`                                                                                                    | Olist uses a per-order customer id; `customer_unique_id` links a person across orders.                                |
| `sellers`                           | seller                     | `seller_id` PK, zip, city, state                                                                                                                                                                         |                                                                                                                       |
| `products`                          | catalog item               | `product_id` PK, `product_category_name` (PT), weight/length/height/width cm, photos_qty                                                                                                                 | Category is Portuguese; join `product_category_name_translation` for English.                                         |
| `product_category_name_translation` | category                   | `product_category_name` PK, `product_category_name_english`                                                                                                                                              |                                                                                                                       |
| `geolocation`                       | zip prefix point           | `id` PK, `geolocation_zip_code_prefix`, lat, lng, city, state                                                                                                                                            |                                                                                                                       |
| `orders`                            | order                      | `order_id` PK, `customer_id` FK, `order_status`, `order_purchase_timestamp` (idx), `order_approved_at`, `order_delivered_carrier_date`, `order_delivered_customer_date`, `order_estimated_delivery_date` | The spine of the replay clock. Statuses: created/approved/invoiced/processing/shipped/delivered/canceled/unavailable. |
| `order_items`                       | order line                 | (`order_id`,`order_item_id`) PK, `product_id` FK, `seller_id` FK, `shipping_limit_date`, `price`, `freight_value`                                                                                        | Revenue = Σ(price); freight = Σ(freight_value).                                                                       |
| `order_payments`                    | payment line               | (`order_id`,`payment_sequential`) PK, `payment_type`, `payment_installments`, `payment_value`                                                                                                            | **Currently not seeded — task D4 fixes this** (billing agent depends on it).                                          |
| `order_reviews`                     | review                     | (`review_id`,`order_id`) PK, `review_score` (idx), title, message, creation/answer timestamps                                                                                                            | **Currently not seeded — task D4** (marketing sentiment / CSAT).                                                      |
| `inventory`                         | product stock snapshot     | `product_id` PK/FK, `quantity_on_hand`, `quantity_reserved`, `quantity_available`, `reorder_point`, `reorder_quantity`, `last_updated`, `data_source`, `data_status`                                     | **Currently never populated; inventory agent fabricates stock from an MD5 hash — task D5 replaces this.**             |

## Warehouse — DataCo supply chain

| Table                | Grain      | Key columns                                                                                                                                                                                                                                                                                                  | Notes                                                                             |
| -------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| `dataco_orders`      | order      | `order_id` PK (int), `customer_id`, segment, city/state/country, market, region, `order_date` (idx), `shipping_date`, `order_status`, `shipping_mode`, `delivery_status`, `late_delivery_risk` (idx), `days_for_shipping_real`, `days_for_shipment_scheduled`, `payment_type`, `order_total`, `order_profit` | Global lanes; powers logistics + pricing (`order_profit`, `days_for_shipping_*`). |
| `dataco_order_items` | order line | `order_item_id` PK, `order_id` FK, `product_card_id`, `product_name`, category/department, `product_price`, `order_item_quantity`, `sales`, discount fields, `order_item_total`, `order_item_profit_ratio`                                                                                                   | Pricing/margin analysis.                                                          |

---

## Operational — identity & audit

| Table               | Purpose                             | Key columns                                                                                                                                                                                                                          |
| ------------------- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `users`             | platform users                      | `id` PK (uuid), `username` uniq, `email` uniq, `hashed_password` (bcrypt), `role` (enum: SUPER_ADMIN / {ORDERS,INVENTORY,CUSTOMER_SUPPORT,PRICING,MARKETING,LOGISTICS}\_ADMIN), `is_active`, timestamps, `last_login_at`             |
| `audit_logs`        | append-only audit trail             | `id` PK, `action` (idx), `actor_id`/`actor_username`/`actor_role`, `target_type`/`target_id`, `status_code`, `latency_ms`, `details` (JSON), `ip_address`, `request_id` (idx), `created_at` (idx) — **no update/delete route**       |
| `notifications`     | operational alerts                  | `id` PK, `title`, `message`, `notification_type`, `severity` (enum), `priority`, `responsible_agent` (idx), `target_role` (idx), `fingerprint` (idx, dedup), `status` (enum UNREAD/READ/ACKNOWLEDGED/RESOLVED), lifecycle timestamps |
| `agent_predictions` | historical agent forecast snapshots | `id` PK, `execution_id` (idx), `snapshot_id`, `agent_id` (idx), `prediction` (JSON), `confidence`, `metrics_json`                                                                                                                    |

## Operational — agent runtime (tasks D2 / A9 / O1 / O3)

| Table                     | Purpose                      | Key columns                                                                                                                                                                              |
| ------------------------- | ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `agent_runs`              | one row per agent execution  | `id` PK, `agent`, `trigger` (api/schedule/orchestrator), `execution_id`, `status`, `started_at`, `finished_at`, `latency_ms`, `token_usage` (JSON), `cost_usd`, `error`                  |
| `agent_findings`          | findings emitted by a run    | `id` PK, `run_id` FK, `agent`, `category`, `severity`, `what_happened`, `why_it_matters`, `recommended_action`, `evidence`, `confidence`, `data_status`, `automation_eligibility` (JSON) |
| `orchestration_decisions` | one row per orchestrator run | `id` PK, `execution_id`, `event_type`, `agents_involved` (JSON), `systemic_findings` (JSON), `conflicts_resolved` (JSON), `execution_plan` (JSON), `created_at`                          |
| `automation_actions`      | executed automation          | `id` PK, `decision_id` FK, `agent`, `action_type`, `mode` (AUTO/APPROVED), `status`, `payload` (JSON), `result` (JSON), `verified` (bool), `rolled_back` (bool), timestamps              |
| `approvals`               | HITL queue                   | `id` PK, `decision_id` FK, `action_type`, `required_role`, `status` (PENDING/APPROVED/REJECTED), `requested_at`, `decided_by`, `decided_at`, `reason`                                    |

## Operational — conversations & memory (tasks A3 / A7)

| Table                   | Purpose                 | Key columns                                                                                                         |
| ----------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `conversations`         | customer support thread | `id` PK, `thread_id` (langgraph), `customer_ref`, `channel`, `created_at`, `last_message_at`, `status`              |
| `conversation_messages` | message                 | `id` PK, `conversation_id` FK, `role` (user/assistant/agent), `content`, `agent`, `tool_calls` (JSON), `created_at` |
| `customer_memory`       | long-term profile       | `customer_ref` PK, `profile` (JSON: preferences, past issues, sentiment trend), `updated_at`                        |
| `langgraph_checkpoints` | graph state saver       | managed by `langgraph-checkpoint-postgres`                                                                          |

## Operational — domain extras (tasks A5 / A6 / A4)

| Table               | Purpose                                                                                                  |
| ------------------- | -------------------------------------------------------------------------------------------------------- |
| `campaigns`         | marketing agent: campaign definitions + measured response by segment                                     |
| `price_experiments` | pricing agent: markdown/again-price recommendations + expected vs realised margin delta                  |
| `carrier_lanes`     | logistics agent: (carrier, origin_region, dest_region) transit-time distribution + SLA + late-rate       |
| `stock_movements`   | inventory ledger: (`product_id`, `ts`, `delta`, `reason`, `order_id?`) — every stock change, append-only |

---

## Indexing highlights (task D7)

- `orders (order_purchase_timestamp)` — every agent range-scans by simulated clock.
- Partial: `orders (order_purchase_timestamp) WHERE order_status IN ('created','approved','invoiced','processing')` — pending backlog.
- `orders (order_status, order_purchase_timestamp)` — status distribution + cancellation series.
- `dataco_orders (order_date)`, `dataco_orders (late_delivery_risk) WHERE late_delivery_risk = 1`.
- `order_items (product_id)`, `order_reviews (order_id)`, `stock_movements (product_id, ts)`.
- `agent_runs (agent, started_at)`, `notifications (responsible_agent, status)`, `audit_logs (created_at)`.

## Conventions

- All timestamps `TIMESTAMPTZ`, stored UTC.
- Surrogate keys: uuid4 strings for operational tables; natural keys retained for warehouse (Olist ids, DataCo ints).
- `data_status` enum on agent-facing outputs: `OBSERVED | CALCULATED | ESTIMATED | MODELLED | NOT_ESTIMABLE`.
- Roles: `commerceos_app` (CRUD on operational, read on warehouse), `commerceos_ro` (SELECT only — analytics agent + replica dashboards).
