"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "./InventoryAgentView.css";
import "./DomainAgentView.css";
import { API_ENDPOINTS } from "./config";
import { renderMarkdown } from "./lib/markdown";
import { humanizeToolName } from "./lib/format";
import { AgentReportingControls } from "./AgentReportingControls";
import { AgentReportData, ChartSeries } from "./lib/reportGenerator";

type InventoryAgentViewProps = {
  monitorUrl?: string;
  queryUrl?: string;
  refreshIntervalMs?: number;
  refreshKey?: number;
};

type AgentResponse = {
  output?: string;
  execution_id?: string;
  snapshot_id?: string;
  status?: string;
  products?: InventoryProduct[];
  low_stock_products?: InventoryProduct[];
  recommendations?: ReorderRecommendation[];
  reorder_suggestions?: ReorderRecommendation[];
  sales_analysis?: SalesAnalysis[];
  alerts?: InventoryAlert[];
  metrics?: Record<string, unknown>;
  actions?: InventoryAction[];
  tool_calls?: ToolCall[];
  [key: string]: unknown;
};

type InventoryProduct = {
  id?: number | string;
  product_id?: number | string;
  sku?: string;
  name?: string;
  category?: string;
  current_stock?: number;
  safety_stock?: number;
  reorder_point?: number;
  daily_sales?: number;
  stockQuantity?: number;
  stock_quantity?: number;
  price?: number;
  available_stock?: number;
  reorder_required?: boolean;
  reorder_flag?: boolean;
  lead_time_days?: number;
  [key: string]: unknown;
};

type ReorderRecommendation = {
  product_id?: number | string;
  sku?: string;
  name?: string;
  current_stock?: number;
  stock_quantity?: number;
  daily_sales?: number;
  average_daily_sales?: number;
  lead_time_days?: number;
  safety_stock?: number;
  reorder_point?: number;
  suggested_quantity?: number;
  recommended_quantity?: number;
  estimated_cost?: number;
  supplier_unit_cost?: number;
  reason?: string;
  [key: string]: unknown;
};

type SalesAnalysis = {
  product_id?: number | string;
  sku?: string;
  name?: string;
  total_sales?: number;
  average_daily_sales?: number;
  sales_velocity?: number;
  trend?: string;
  reorder_quantity?: number;
  [key: string]: unknown;
};

type InventoryAlert = {
  id?: string | number;
  product_id?: string | number;
  sku?: string;
  name?: string;
  severity?: string;
  message?: string;
  reason?: string;
  created_at?: string;
  timestamp?: string;
  [key: string]: unknown;
};

type InventoryAction = {
  action?: string;
  status?: string;
  product_id?: string | number;
  message?: string;
  [key: string]: unknown;
};

type ToolCall = {
  tool?: string;
  name?: string;
  input?: unknown;
  output?: unknown;
};

function getAuthHeaders(): HeadersInit {
  if (typeof window === "undefined") {
    return {
      "Content-Type": "application/json",
    };
  }

  const token =
    localStorage.getItem("access_token") ||
    localStorage.getItem("token") ||
    sessionStorage.getItem("access_token") ||
    sessionStorage.getItem("token");

  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

function toNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }

  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  return null;
}

function formatNumber(value: unknown): string {
  const number = toNumber(value);

  if (number === null) {
    return "0";
  }

  return new Intl.NumberFormat().format(number);
}

function formatCurrency(value: unknown): string {
  const number = toNumber(value);

  if (number === null) {
    return "R$0.00";
  }

  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "BRL",
    maximumFractionDigits: 2,
  }).format(number);
}

function extractArray<T>(response: AgentResponse, keys: string[]): T[] {
  for (const key of keys) {
    const value = response[key];

    if (Array.isArray(value)) {
      return value as T[];
    }
  }

  return [];
}

function getStock(product: InventoryProduct): number | null {
  return (
    toNumber(product.current_stock) ??
    toNumber(product.stockQuantity) ??
    toNumber(product.stock_quantity) ??
    toNumber(product.available_stock)
  );
}

function getProductId(product: InventoryProduct) {
  return product.id ?? product.product_id ?? product.sku ?? "P-1001";
}

function getRecommendationQuantity(recommendation: ReorderRecommendation): number | null {
  return (
    toNumber(recommendation.suggested_quantity) ??
    toNumber(recommendation.recommended_quantity) ??
    toNumber(recommendation.reorder_quantity)
  );
}

const INVENTORY_SUGGESTIONS = [
  "Which products are below threshold?",
  "What's the reorder point for the lowest-stock item?",
  "Which products are selling fastest?",
  "What are the top reorder recommendations?",
];

export default function InventoryAgentView({
  monitorUrl = API_ENDPOINTS.inventory.monitor,
  queryUrl = API_ENDPOINTS.inventory.query,
  refreshIntervalMs = 60000,
  refreshKey,
}: InventoryAgentViewProps) {
  const [data, setData] = useState<AgentResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [monitoring, setMonitoring] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  // Chat — same shape/behavior as the other agent assistants so the
  // interface is consistent across the whole dashboard: a running
  // conversation log (not a single-shot query box), with the last few turns
  // sent back on each ask so the agent can resolve follow-up questions.
  const [input, setInput] = useState("");
  const [chat, setChat] = useState<{ role: "user" | "agent"; text: string; llm?: boolean }[]>([]);
  const [asking, setAsking] = useState(false);
  const chatRef = useRef<HTMLDivElement | null>(null);

  const [selectedProduct, setSelectedProduct] = useState<string | number | null>(null);

  const runMonitor = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);

      const response = await fetch(monitorUrl, {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({}),
      });

      if (!response.ok) {
        const message = await response.text();
        throw new Error(message || `Inventory API returned ${response.status}`);
      }

      const result = (await response.json()) as AgentResponse;

      setData(result);
      setLastUpdated(new Date());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load inventory agent data.");
    } finally {
      setLoading(false);
      setMonitoring(false);
    }
  }, [monitorUrl]);

  const runManualMonitor = async () => {
    setMonitoring(true);
    await runMonitor();
  };

  const ask = async (raw: string) => {
    const q = raw.trim();
    if (!q || asking) return;
    const history = chat
      .slice(-6)
      .map((m) => ({ role: m.role === "user" ? "user" : "assistant", text: m.text }));
    setChat((c) => [...c, { role: "user", text: q }]);
    setInput("");
    setAsking(true);
    try {
      const response = await fetch(queryUrl, {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({ query: q, history }),
      });
      if (!response.ok) {
        const message = await response.text();
        throw new Error(message || `Inventory query returned ${response.status}`);
      }
      const result = (await response.json()) as AgentResponse & { llm_backed?: boolean };
      const outputText =
        typeof result.output === "string" ? result.output : JSON.stringify(result, null, 2);
      setChat((c) => [...c, { role: "agent", text: outputText, llm: result.llm_backed }]);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Inventory agent query failed.";
      setChat((c) => [...c, { role: "agent", text: `⚠️ ${msg}` }]);
    } finally {
      setAsking(false);
    }
  };

  useEffect(() => {
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: "smooth" });
  }, [chat, asking]);

  // Nothing auto-runs on mount: fields stay empty until the user explicitly
  // runs the watchdog inspection.
  useEffect(() => {
    if (refreshKey !== undefined && refreshKey > 0) {
      runMonitor();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!refreshIntervalMs || refreshIntervalMs <= 0) {
      return;
    }

    const interval = window.setInterval(() => {
      runMonitor();
    }, refreshIntervalMs);

    return () => window.clearInterval(interval);
  }, [refreshIntervalMs, runMonitor]);

  useEffect(() => {
    if (refreshKey !== undefined && refreshKey > 0) {
      runMonitor();
    }
  }, [refreshKey, runMonitor]);

  const products = useMemo(
    () =>
      extractArray<InventoryProduct>(data ?? {}, ["products", "low_stock_products", "inventory"]),
    [data],
  );

  const recommendations = useMemo(
    () =>
      extractArray<ReorderRecommendation>(data ?? {}, [
        "recommendations",
        "reorder_suggestions",
        "reorderRecommendations",
      ]),
    [data],
  );

  const salesAnalysis = useMemo(
    () =>
      extractArray<SalesAnalysis>(data ?? {}, ["sales_analysis", "salesAnalysis", "sales_trends"]),
    [data],
  );

  const alerts = useMemo(
    () => extractArray<InventoryAlert>(data ?? {}, ["alerts", "inventory_alerts"]),
    [data],
  );

  const actions = useMemo(
    () => extractArray<InventoryAction>(data ?? {}, ["actions", "executed_actions"]),
    [data],
  );

  const toolCalls = useMemo(
    () => extractArray<ToolCall>(data ?? {}, ["tool_calls", "toolCalls"]),
    [data],
  );

  const metrics = data?.metrics ?? {};

  const totalProducts =
    toNumber(metrics.total_products) ?? toNumber(metrics.totalProducts) ?? products.length;

  const lowStockCount =
    toNumber(metrics.low_stock_count) ??
    toNumber(metrics.lowStockCount) ??
    products.filter((p) => (getStock(p) ?? Infinity) < 50).length;

  const reorderCount =
    toNumber(metrics.reorder_count) ?? toNumber(metrics.reorderCount) ?? recommendations.length;

  const inventoryValue =
    toNumber(metrics.inventory_value) ??
    toNumber(metrics.inventoryValue) ??
    products.reduce((acc, p) => acc + (getStock(p) || 0) * (toNumber(p.price) || 0), 0);

  const buildReportData = useCallback((): AgentReportData => {
    const lowStockSeries: ChartSeries[] = products
      .filter((p: InventoryProduct) => (getStock(p) ?? Infinity) < 50)
      .slice(0, 6)
      .map((p: InventoryProduct) => ({
        label: String(p.name || p.sku || `Item #${p.id || p.product_id || "unknown"}`),
        value: getStock(p) || 0,
        color: (getStock(p) || 0) < 10 ? "#ef4444" : "#f59e0b",
      }));

    const stockHealthSeries: ChartSeries[] = [
      { label: "Healthy Stock", value: Math.max(0, totalProducts - lowStockCount), color: "#10b981" },
      { label: "Low Stock Items", value: lowStockCount, color: "#f59e0b" },
      { label: "Critical Reorders", value: reorderCount, color: "#ef4444" },
    ];

    const reportFindings = alerts.map((a: InventoryAlert) => ({
      title: String(a.name || "Inventory Stock Alert"),
      severity: String(a.severity || "MEDIUM"),
      category: "Inventory Level",
      whatHappened: String(a.message || a.reason || "Stock level has crossed safety replenishment thresholds."),
      whyItMatters: "Running out of popular inventory leads to lost sales and disappointed customers.",
      recommendedAction: "Trigger immediate replenishment order to suppliers.",
    }));

    const recs = recommendations.map((r: ReorderRecommendation, i: number) => ({
      title: `Restock ${String(r.name || r.sku || `Product #${r.product_id || i + 1}`)}`,
      detail: `Reorder suggested quantity: ${getRecommendationQuantity(r) ?? 50} units from supplier.`,
      expectedImpact: "Prevents stockouts and fulfills incoming order demand seamlessly.",
      priority: String(i + 1),
    }));

    return {
      agentName: "Inventory Agent",
      agentRole: "Smart Inventory Watchdog & Demand Velocity Profiling",
      timestamp: lastUpdated ? lastUpdated.toLocaleString() : new Date().toLocaleString(),
      health: lowStockCount > 20 ? "Needs Attention" : "Healthy",
      confidencePct: 92,
      summary: `Currently monitoring ${totalProducts} catalog items. ${lowStockCount} items are running below safe threshold levels with ${reorderCount} active reorder recommendations.`,
      metrics: [
        { label: "Catalog Products", value: totalProducts },
        { label: "Low Stock Count", value: lowStockCount },
        { label: "Reorder Triggers", value: reorderCount },
        { label: "Inventory Value", value: `$${Math.round(inventoryValue).toLocaleString()}` },
      ],
      findings: reportFindings,
      recommendations: recs,
      charts: [
        { title: "INVENTORY STOCK HEALTH", type: "donut", data: stockHealthSeries },
        { title: "LOWEST STOCK PRODUCTS (UNITS ON HAND)", type: "bar", data: lowStockSeries },
      ],
    };
  }, [products, alerts, recommendations, totalProducts, lowStockCount, reorderCount, inventoryValue, lastUpdated]);

  return (
    <section className="inventory-view-container">
      {/* Header */}
      <div className="inv-header">
        <div>
          <div className="inv-badge">
            <span className="inv-badge-dot" />
            <span>Inventory Agent</span>
          </div>

          <h2 className="inv-header-title">Smart Inventory Watchdog</h2>

          <p className="inv-header-desc">
            Live inventory monitoring, demand velocity profiling, and automated reorder triggers.
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
          {lastUpdated && (
            <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
              Updated {lastUpdated.toLocaleTimeString()}
            </span>
          )}

          <AgentReportingControls
            agentName="Inventory Agent"
            generateReportData={buildReportData}
            disabled={!data || loading}
          />

          <button
            onClick={runManualMonitor}
            disabled={loading || monitoring}
            className="inv-btn-secondary"
          >
            {loading || monitoring ? "Running..." : "Run Monitor"}
          </button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div
          style={{
            marginTop: "16px",
            padding: "14px 18px",
            borderRadius: "12px",
            background: "var(--status-danger-bg)",
            border: "1px solid rgba(185, 28, 28, 0.35)",
            color: "var(--status-danger)",
            fontSize: "13px",
          }}
        >
          <strong>Inventory Agent note:</strong> {error}
        </div>
      )}

      {/* Dynamic Metrics */}
      <div className="inv-metrics-grid">
        <MetricCard
          title="Products Catalog"
          value={formatNumber(totalProducts)}
          description="Monitored catalog items"
        />

        <MetricCard
          title="Low Stock Items"
          value={formatNumber(lowStockCount)}
          description="Watchdog threshold triggers"
        />

        <MetricCard
          title="Reorder Candidates"
          value={formatNumber(reorderCount)}
          description="ROP & EOQ recommendations"
        />

        <MetricCard
          title="Inventory Valuation"
          value={formatCurrency(inventoryValue)}
          description="Empirical catalog value"
        />
      </div>

      {/* Agent Output */}
      {data?.output && (
        <div className="inv-section">
          <SectionTitle
            title="Agent Analysis"
            subtitle="Latest empirical evaluation generated by the Smart Inventory Watchdog"
          />

          <div className="inv-output-box">{renderMarkdown(data.output) ?? data.output}</div>
        </div>
      )}

      {/* Alerts */}
      <div className="inv-section">
        <SectionTitle
          title="Inventory Alerts"
          subtitle="Real-time notifications generated for supply chain and inventory controllers"
        />

        {alerts.length === 0 ? (
          <EmptyState message="No inventory alerts were returned by the agent." />
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "12px", marginTop: "16px" }}>
            {alerts.map((alert, index) => (
              <div
                key={String(alert.id ?? index)}
                style={{
                  background: "var(--bg-elevated)",
                  border: "1px solid var(--border)",
                  borderRadius: "12px",
                  padding: "16px",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "flex-start",
                  gap: "16px",
                }}
              >
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <span
                      style={{ fontWeight: 700, color: "var(--text-primary)", fontSize: "14px" }}
                    >
                      {alert.name ?? alert.sku ?? `Product ${alert.product_id ?? ""}`}
                    </span>

                    {alert.severity && (
                      <span
                        style={{
                          borderRadius: "9999px",
                          padding: "2px 8px",
                          fontSize: "11px",
                          fontWeight: 700,
                          background:
                            alert.severity === "CRITICAL"
                              ? "var(--status-danger-bg)"
                              : "var(--status-warning-bg)",
                          color:
                            alert.severity === "CRITICAL"
                              ? "var(--status-danger)"
                              : "var(--status-warning)",
                          border:
                            alert.severity === "CRITICAL"
                              ? "1px solid rgba(185, 28, 28, 0.3)"
                              : "1px solid rgba(180, 83, 9, 0.3)",
                        }}
                      >
                        {alert.severity}
                      </span>
                    )}
                  </div>

                  <p
                    style={{
                      margin: "8px 0 0 0",
                      fontSize: "13px",
                      color: "var(--text-secondary)",
                    }}
                  >
                    {alert.message ?? alert.reason ?? "Inventory alert"}
                  </p>
                </div>

                <span
                  style={{ fontSize: "11px", color: "var(--text-muted)", whiteSpace: "nowrap" }}
                >
                  {alert.timestamp ?? alert.created_at ?? ""}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Low Stock / Inventory */}
      <div className="inv-section">
        <SectionTitle
          title="Inventory Watchdog Catalog"
          subtitle="Products monitored during the current operational cycle"
        />

        {products.length === 0 ? (
          <EmptyState message="No products were returned by the current monitoring response." />
        ) : (
          <div className="inv-table-wrapper" style={{ marginTop: "16px" }}>
            <table className="inv-table">
              <thead>
                <tr>
                  <th>Product</th>
                  <th>SKU</th>
                  <th>Category</th>
                  <th>Current Stock</th>
                  <th>Safety Stock</th>
                  <th>ROP</th>
                  <th>Unit Price</th>
                  <th>Watchdog Status</th>
                </tr>
              </thead>

              <tbody>
                {products.map((product, index) => {
                  const productId = getProductId(product);
                  const stock = getStock(product);
                  const rop = toNumber(product.reorder_point);
                  const isLowStock =
                    product.reorder_required ||
                    product.reorder_flag ||
                    (stock !== null && rop !== null && stock <= rop);

                  return (
                    <tr key={`${productId}-${index}`}>
                      <td>
                        <button
                          onClick={() =>
                            setSelectedProduct(selectedProduct === productId ? null : productId)
                          }
                          style={{
                            background: "none",
                            border: "none",
                            padding: 0,
                            cursor: "pointer",
                            fontWeight: 600,
                            color: "var(--text-primary)",
                            textAlign: "left",
                          }}
                        >
                          {product.name ?? `Product ${productId}`}
                        </button>
                      </td>

                      <td style={{ color: "var(--text-secondary)" }}>
                        {product.sku ?? `SKU-${String(productId).slice(0, 8).toUpperCase()}`}
                      </td>

                      <td style={{ color: "var(--text-secondary)" }}>
                        {product.category ?? "General Merchandise"}
                      </td>

                      <td>
                        <span
                          style={{
                            fontWeight: 700,
                            color: isLowStock ? "var(--status-warning)" : "var(--status-success)",
                          }}
                        >
                          {formatNumber(stock)}
                        </span>
                      </td>

                      <td style={{ color: "var(--text-secondary)" }}>
                        {formatNumber(product.safety_stock)}
                      </td>

                      <td style={{ fontWeight: 700, color: "var(--accent-primary)" }}>
                        {formatNumber(product.reorder_point)}
                      </td>

                      <td style={{ color: "var(--text-secondary)" }}>
                        {formatCurrency(product.price)}
                      </td>

                      <td>
                        {isLowStock ? (
                          <span className="inv-badge-reorder">Reorder Triggered</span>
                        ) : (
                          <span style={{ fontSize: "11px", color: "var(--status-success)" }}>
                            Optimal Stock
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Reorder Recommendations */}
      <div className="inv-section">
        <SectionTitle
          title="Reorder Recommendations"
          subtitle="Statistical Reorder Points (ROP) and Economic Order Quantity (EOQ) suggestions"
        />

        {recommendations.length === 0 ? (
          <EmptyState message="No reorder recommendations returned." />
        ) : (
          <div className="inv-table-wrapper" style={{ marginTop: "16px" }}>
            <table className="inv-table">
              <thead>
                <tr>
                  <th>Product</th>
                  <th>Current Stock</th>
                  <th>Daily Sales</th>
                  <th>Lead Time</th>
                  <th>Safety Stock</th>
                  <th>ROP</th>
                  <th>Suggested Batch</th>
                  <th>Est. Cost</th>
                </tr>
              </thead>

              <tbody>
                {recommendations.map((item, index) => (
                  <tr key={`${item.product_id ?? item.sku ?? index}`}>
                    <td style={{ color: "var(--text-primary)", fontWeight: 600 }}>
                      {item.name ?? item.sku ?? `Product ${item.product_id ?? ""}`}
                    </td>

                    <td style={{ color: "var(--text-secondary)" }}>
                      {formatNumber(item.current_stock ?? item.stock_quantity)}
                    </td>

                    <td style={{ color: "var(--text-secondary)" }}>
                      {formatNumber(item.daily_sales ?? item.average_daily_sales)}
                    </td>

                    <td style={{ color: "var(--text-secondary)" }}>
                      {item.lead_time_days !== undefined && item.lead_time_days !== null
                        ? `${formatNumber(item.lead_time_days)}d`
                        : "—"}
                    </td>

                    <td style={{ color: "var(--text-secondary)" }}>
                      {formatNumber(item.safety_stock)}
                    </td>

                    <td style={{ fontWeight: 700, color: "var(--accent-primary)" }}>
                      {formatNumber(item.reorder_point)}
                    </td>

                    <td style={{ fontWeight: 700, color: "var(--status-success)" }}>
                      {formatNumber(getRecommendationQuantity(item))} units
                    </td>

                    <td style={{ color: "var(--text-secondary)" }}>
                      {formatCurrency(item.estimated_cost ?? item.supplier_unit_cost)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Sales Analysis */}
      <div className="inv-section">
        <SectionTitle
          title="Sales Velocity & Trend Analysis"
          subtitle="30-day moving sales velocity and replenishment demand"
        />

        {salesAnalysis.length === 0 ? (
          <EmptyState message="No sales-analysis records returned." />
        ) : (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
              gap: "16px",
              marginTop: "16px",
            }}
          >
            {salesAnalysis.map((item, index) => (
              <div
                key={`${item.product_id ?? item.sku ?? index}`}
                style={{
                  background: "var(--bg-elevated)",
                  border: "1px solid var(--border)",
                  borderRadius: "12px",
                  padding: "16px",
                }}
              >
                <h4
                  style={{
                    margin: "0 0 12px 0",
                    fontSize: "14px",
                    fontWeight: 600,
                    color: "var(--text-primary)",
                  }}
                >
                  {item.name ?? item.sku ?? `Product ${item.product_id ?? ""}`}
                </h4>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
                  <MiniMetric label="Total Sales" value={formatNumber(item.total_sales)} />

                  <MiniMetric
                    label="Daily Sales"
                    value={formatNumber(item.average_daily_sales ?? item.sales_velocity)}
                  />

                  <MiniMetric label="Trajectory" value={item.trend ?? "STABLE"} />

                  <MiniMetric label="Reorder Qty" value={formatNumber(item.reorder_quantity)} />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="inv-section">
        <SectionTitle
          title="Agent Actions"
          subtitle="Proactive adjustments and purchase orders issued by the Watchdog"
        />

        {actions.length === 0 ? (
          <EmptyState message="No actions were returned by this execution." />
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "10px", marginTop: "16px" }}>
            {actions.map((action, index) => (
              <div
                key={index}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  background: "var(--bg-elevated)",
                  border: "1px solid var(--border)",
                  borderRadius: "12px",
                  padding: "14px 16px",
                }}
              >
                <div>
                  <p
                    style={{
                      margin: 0,
                      fontSize: "13px",
                      fontWeight: 600,
                      color: "var(--text-primary)",
                    }}
                  >
                    {action.action ?? action.message ?? "Inventory action"}
                  </p>

                  {action.product_id !== undefined && (
                    <p
                      style={{ margin: "4px 0 0 0", fontSize: "11px", color: "var(--text-muted)" }}
                    >
                      Product: {String(action.product_id)}
                    </p>
                  )}
                </div>

                <span style={{ fontSize: "11px", fontWeight: 700, color: "var(--status-success)" }}>
                  {action.status ?? "EXECUTED"}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Interactive Agent — same chat layout as every other agent assistant */}
      <div className="dav-section" style={{ ["--accent" as string]: "var(--agent-inventory)" }}>
        <h3 className="dav-section-title">Ask the Inventory Agent</h3>
        <div className="dav-chat" ref={chatRef}>
          {chat.length === 0 && (
            <div className="dav-chat-empty">
              <p>Ask a question about stock, reorders, or demand.</p>
              <div className="dav-suggestions">
                {INVENTORY_SUGGESTIONS.map((s) => (
                  <button key={s} className="dav-suggestion" onClick={() => setInput(s)}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {chat.map((m, i) => (
            <div
              key={i}
              className={"dav-msg " + (m.role === "user" ? "dav-msg-user" : "dav-msg-agent")}
            >
              <div className="dav-msg-avatar" aria-hidden="true">{m.role === "user" ? "U" : "I"}</div>
              <div className="dav-msg-col">
                {m.role === "agent" && (
                  <span className={"dav-msg-tag " + (m.llm ? "dav-tag-llm" : "dav-tag-det")}>
                    {m.llm ? "AI Reasoned" : "Data Lookup"}
                  </span>
                )}
                <div className="dav-msg-body">{renderMarkdown(m.text) ?? m.text}</div>
              </div>
            </div>
          ))}
          {asking && (
            <div className="dav-msg dav-msg-agent">
              <div className="dav-msg-avatar" aria-hidden="true">I</div>
              <div className="dav-msg-col">
                <div className="dav-msg-body">
                  <span className="dav-msg-thinking"><span /><span /><span /></span>
                </div>
              </div>
            </div>
          )}
        </div>
        <div className="dav-input-row">
          <input
            className="dav-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && ask(input)}
            placeholder="Ask the Inventory Agent…"
          />
          <button
            className="dav-btn dav-btn-primary"
            disabled={asking || !input.trim()}
            onClick={() => ask(input)}
          >
            {asking ? "…" : "Send"}
          </button>
        </div>
      </div>

      {/* Tool Calls */}
      {toolCalls.length > 0 && (
        <div className="inv-section">
          <SectionTitle
            title="Agent Tool Execution"
            subtitle="Transparent ReAct tool traces executed during the monitoring cycle"
          />

          <div style={{ display: "flex", flexDirection: "column", gap: "10px", marginTop: "16px" }}>
            {toolCalls.map((call, index) => (
              <details
                key={index}
                style={{
                  background: "var(--bg-elevated)",
                  border: "1px solid var(--border)",
                  borderRadius: "12px",
                  overflow: "hidden",
                }}
              >
                <summary
                  style={{
                    padding: "12px 16px",
                    cursor: "pointer",
                    fontWeight: 600,
                    fontSize: "13px",
                    color: "var(--text-primary)",
                  }}
                >
                  🔧 {humanizeToolName(call.tool ?? call.name ?? `Step ${index + 1}`)}
                </summary>

                <div
                  style={{
                    borderTop: "1px solid var(--border)",
                    padding: "14px 16px",
                    background: "var(--bg-sunken)",
                    fontSize: "12.5px",
                    color: "var(--text-secondary)",
                    lineHeight: 1.6,
                  }}
                >
                  {typeof call.output === "string" ? (
                    (renderMarkdown(call.output) ?? call.output)
                  ) : (
                    <pre
                      style={{
                        margin: 0,
                        fontSize: "11px",
                        whiteSpace: "pre-wrap",
                        overflowX: "auto",
                      }}
                    >
                      {JSON.stringify(call.output, null, 2)}
                    </pre>
                  )}
                </div>
              </details>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function MetricCard({
  title,
  value,
  description,
}: {
  title: string;
  value: string;
  description: string;
}) {
  return (
    <div className="inv-card">
      <p className="inv-card-title">{title}</p>

      <p className="inv-card-value">{value}</p>

      <p className="inv-card-desc">{description}</p>
    </div>
  );
}

function MiniMetric({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        background: "var(--bg-surface)",
        border: "1px solid var(--border)",
        borderRadius: "8px",
        padding: "10px",
      }}
    >
      <p
        style={{
          margin: 0,
          fontSize: "10px",
          fontWeight: 700,
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          color: "var(--text-muted)",
        }}
      >
        {label}
      </p>

      <p
        style={{
          margin: "4px 0 0 0",
          fontSize: "13px",
          fontWeight: 700,
          color: "var(--text-primary)",
        }}
      >
        {value}
      </p>
    </div>
  );
}

function SectionTitle({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="inv-section-header">
      <h3 className="inv-section-title">{title}</h3>

      <p className="inv-section-subtitle">{subtitle}</p>
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return <div className="inv-empty">{message}</div>;
}
