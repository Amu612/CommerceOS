"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "./DomainAgentView.css";
import { renderMarkdown } from "./lib/markdown";
import { AgentReportingControls } from "./AgentReportingControls";
import { AgentReportData, ChartSeries } from "./lib/reportGenerator";
import { CesiumRouteViewer } from "./components/CesiumRouteViewer";

type OrderRouteData = {
  status: string;
  reason?: string;
  order_id: string;
  order_status: string;
  purchase_date?: string;
  estimated_delivery_date?: string;
  actual_delivery_date?: string;
  delivery_delay_days?: number | null;
  transit_days?: number | null;
  is_late?: boolean;
  freight_value: number;
  order_subtotal: number;
  total_value: number;
  review_score?: number | null;
  review_comment?: string | null;
  origin: {
    seller_id: string;
    zip_code_prefix: number;
    city: string;
    state: string;
    lat: number;
    lng: number;
    item_count: number;
  };
  destination: {
    customer_id: string;
    customer_unique_id: string;
    zip_code_prefix: number;
    city: string;
    state: string;
    lat: number;
    lng: number;
  };
  sellers: {
    seller_id: string;
    zip_code_prefix: number;
    city: string;
    state: string;
    lat: number;
    lng: number;
    item_count: number;
    freight_value: number;
    subtotal_value: number;
  }[];
  selected_seller_id: string;
  has_multiple_sellers: boolean;
  route: {
    status: string;
    distance_km: number;
    duration_hours: number;
    duration_formatted: string;
    geometry: [number, number][];
    service: string;
  };
  logistics_intelligence: {
    risk_level: string;
    reasons: string[];
    cross_state: boolean;
    origin_state: string;
    dest_state: string;
  };
};

type RouteSample = { order_id: string; label: string };

const FALLBACK_ROUTE_SAMPLES: RouteSample[] = [
  { order_id: "000229ec398224ef6ca0657da4fc703e", label: "Single Seller" },
  { order_id: "00bcee890eba57a9767c7b5ca12d3a1b", label: "Multi-Seller" },
  { order_id: "013a98b3a668bcef05b98898177f6923", label: "Multi-Origin" },
];

type MetricCard = { label: string; value: unknown; unit?: string; description?: string; data_status?: string };
type Finding = {
  category: string;
  severity: string;
  title: string;
  what_happened: string;
  why_it_matters: string;
  recommended_action: string;
  evidence?: string;
  confidence?: number;
  sample_count?: number;
};
type Recommendation = { title: string; detail: string; expected_impact?: string; priority?: string };
type AnalysisOutput = {
  agent: string;
  execution_id: string;
  timestamp: string;
  status: string;
  confidence: number;
  health: string;
  summary: string;
  metrics: MetricCard[];
  findings: Finding[];
  recommendations: Recommendation[];
  charts: Record<string, unknown>;
  llm_backed: boolean;
  not_estimable_reason?: string | null;
};

export type DomainAgentViewProps = {
  agentKey: string;
  title: string;
  subtitle: string;
  accent: string; // hex
  analyzeUrl: string;
  queryUrl: string;
  routeUrl?: string;
  refreshKey?: number;
  suggestions?: string[];
  /** Optional extra control rendered under the header — e.g. Pricing's
   * competitor-price-feed toggle. Independent of the data-source switch. */
  headerExtra?: React.ReactNode;
};

const HEALTH_COLOR: Record<string, string> = {
  HEALTHY: "var(--status-success)",
  NEEDS_ATTENTION: "var(--status-warning)",
  CRITICAL: "var(--status-danger)",
  NOT_ESTIMABLE: "var(--status-neutral)",
};
const HEALTH_LABEL: Record<string, string> = {
  HEALTHY: "Healthy",
  NEEDS_ATTENTION: "Needs Attention",
  CRITICAL: "Critical",
  NOT_ESTIMABLE: "Not Estimable",
};
const SEV_COLOR: Record<string, string> = {
  CRITICAL: "var(--status-danger)",
  HIGH: "#c2410c",
  MEDIUM: "var(--status-warning)",
  LOW: "#0369a1",
};

function fmt(v: unknown): string {
  if (typeof v === "number") return new Intl.NumberFormat().format(v);
  return String(v ?? "—");
}

const ACRONYMS = new Set(["sla", "rop", "eoq", "roi", "csat", "ltv", "cac", "sku", "id"]);

/** Turns a raw snake_case field name (as returned by the backend) into a
 * short, title-cased header — e.g. "at_risk_rate_pct" -> "At Risk Rate",
 * with the unit shown in the cell instead (see `fmtCell`) rather than
 * cluttering the header with "_pct"/"_days". */
function humanizeKey(key: string): string {
  const stripped = key.replace(/_(pct|percent|days?)$/i, "");
  return stripped
    .split("_")
    .filter(Boolean)
    .map((w) => (ACRONYMS.has(w.toLowerCase()) ? w.toUpperCase() : w.charAt(0).toUpperCase() + w.slice(1)))
    .join(" ");
}

/** Renders a chart-table cell with its unit inferred from the field name
 * (percent, days, currency) so a bare number is never shown without context
 * the header alone dropped. */
function fmtCell(key: string, v: unknown): string {
  if (typeof v === "number") {
    const kl = key.toLowerCase();
    if (kl.endsWith("_pct") || kl.endsWith("percent")) return `${v}%`;
    if (kl.endsWith("_days") || kl === "days") return `${v}d`;
    if (/(revenue|price|cost|value|total|freight|discount|margin_amount)/.test(kl)) {
      return new Intl.NumberFormat("en-US", { style: "currency", currency: "BRL", maximumFractionDigits: 2 }).format(v);
    }
    return new Intl.NumberFormat().format(v);
  }
  return String(v ?? "—");
}

export default function DomainAgentView({
  agentKey,
  title,
  subtitle,
  accent,
  analyzeUrl,
  queryUrl,
  routeUrl,
  refreshKey,
  suggestions = [],
  headerExtra,
}: DomainAgentViewProps) {
  const [data, setData] = useState<AnalysisOutput | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Logistics Route Intelligence state (used when agentKey === "logistics")
  // Initially no order entered -> shows full interactive 3D globe
  const [orderInput, setOrderInput] = useState("");
  const [routeData, setRouteData] = useState<OrderRouteData | null>(null);
  const [routeLoading, setRouteLoading] = useState(false);
  const [routeError, setRouteError] = useState<string | null>(null);
  // Quick-pick buttons must reference orders that actually exist in THIS
  // deployment's database (it streams progressively), so the samples are
  // fetched from the backend instead of hardcoding full-dataset IDs.
  const [routeSamples, setRouteSamples] = useState<RouteSample[]>(FALLBACK_ROUTE_SAMPLES);

  const fetchRoute = useCallback(async (oid: string, sid?: string) => {
    if (!routeUrl || !oid.trim()) return;
    setRouteLoading(true);
    setRouteError(null);
    try {
      const url = `${routeUrl}?order_id=${encodeURIComponent(oid.trim())}${sid ? `&seller_id=${encodeURIComponent(sid)}` : ""}`;
      const res = await fetch(url, { method: "GET" });
      if (!res.ok) throw new Error(`Route lookup failed (${res.status})`);
      const json: OrderRouteData = await res.json();
      if (json.status !== "OK") {
        throw new Error(json.reason || "Unable to resolve route for this order.");
      }
      setRouteData(json);
    } catch (err) {
      setRouteError(err instanceof Error ? err.message : "Route lookup failed.");
      setRouteData(null);
    } finally {
      setRouteLoading(false);
    }
  }, [routeUrl]);

  useEffect(() => {
    if (!routeUrl) return;
    let cancelled = false;
    fetch(`${routeUrl}/samples`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled || !d) return;
        const items: RouteSample[] = (d.items || [])
          .filter((x: { order_id?: string }) => x && typeof x.order_id === "string")
          .map((x: { order_id: string; label?: string }) => ({ order_id: x.order_id, label: x.label || "Sample" }));
        if (items.length > 0) setRouteSamples(items);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [routeUrl]);

  const [input, setInput] = useState("");
  const [chat, setChat] = useState<{ role: "user" | "agent"; text: string; llm?: boolean }[]>([]);
  const [asking, setAsking] = useState(false);
  const chatRef = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(analyzeUrl, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      if (!r.ok) throw new Error(`${title} returned ${r.status}`);
      setData((await r.json()) as AnalysisOutput);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load agent");
    } finally {
      setLoading(false);
    }
  }, [analyzeUrl, title]);

  // Nothing auto-runs on mount: the panel stays empty until the user
  // explicitly runs the analysis (or a parent triggers refreshKey).
  const firstLoad = useRef(true);
  useEffect(() => {
    if (firstLoad.current && !refreshKey) {
      firstLoad.current = false;
      setLoading(false);
      return;
    }
    firstLoad.current = false;
    load();
  }, [load, refreshKey]);

  useEffect(() => {
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: "smooth" });
  }, [chat, asking]);

  const ask = async (raw: string) => {
    const q = raw.trim();
    if (!q || asking) return;
    // Last few turns, sent so the agent can resolve follow-up questions
    // ("what about its status?") to something mentioned earlier.
    const history = chat.slice(-6).map((m) => ({ role: m.role === "user" ? "user" : "assistant", text: m.text }));
    setChat((c) => [...c, { role: "user", text: q }]);
    setInput("");
    setAsking(true);
    try {
      const r = await fetch(queryUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q, history }),
      });
      const j = await r.json();
      setChat((c) => [...c, { role: "agent", text: j.answer || "No answer.", llm: j.llm_backed }]);
    } catch {
      setChat((c) => [...c, { role: "agent", text: "⚠️ Request failed." }]);
    } finally {
      setAsking(false);
    }
  };

  const chartTables = useMemo(() => {
    const charts = data?.charts ?? {};
    return (
      Object.entries(charts).filter(
        ([, v]) => Array.isArray(v) && v.length > 0 && typeof (v as unknown[])[0] === "object",
      ) as [string, Record<string, unknown>[]][]
    );
  }, [data?.charts]);

  const buildReportData = useCallback((): AgentReportData => {
    const reportCharts: AgentReportData["charts"] = [];
    if (data?.charts) {
      Object.entries(data.charts).forEach(([name, val]) => {
        if (Array.isArray(val) && val.length > 0 && typeof val[0] === "object") {
          const series: ChartSeries[] = [];
          val.slice(0, 8).forEach((item: Record<string, unknown>) => {
            const label = String(item.carrier || item.lane || item.segment || item.category || item.name || item.id || Object.values(item)[0] || "Item");
            const rawVal = item.units ?? item.revenue ?? item.volume ?? item.orders ?? item.late_pct ?? item.count ?? item.value ?? Object.values(item).find(v => typeof v === "number") ?? 0;
            const num = typeof rawVal === "number" ? rawVal : parseFloat(String(rawVal)) || 0;
            series.push({ label, value: Math.round(num) });
          });
          if (series.length > 0) {
            reportCharts.push({
              title: name.replace(/_/g, " ").toUpperCase(),
              type: series.length <= 5 ? "donut" : "bar",
              data: series,
            });
          }
        }
      });
    }

    return {
      agentName: `${title} (${agentKey.toUpperCase()})`,
      agentRole: subtitle,
      timestamp: data?.timestamp ? new Date(data.timestamp).toLocaleString() : new Date().toLocaleString(),
      health: data?.health ? HEALTH_LABEL[data.health] || data.health : "Operating",
      confidencePct: data?.confidence ? Math.round(data.confidence * 100) : 85,
      summary: data?.summary || "Operational analysis generated successfully.",
      metrics: (data?.metrics || []).map((m) => ({
        label: m.label,
        value: fmt(m.value),
        unit: m.unit,
        description: m.description,
      })),
      findings: (data?.findings || []).map((f) => ({
        title: f.title,
        severity: f.severity,
        category: f.category,
        whatHappened: f.what_happened,
        whyItMatters: f.why_it_matters,
        recommendedAction: f.recommended_action,
      })),
      recommendations: (data?.recommendations || []).map((r) => ({
        title: r.title,
        detail: r.detail,
        expectedImpact: r.expected_impact,
        priority: r.priority,
      })),
      charts: reportCharts,
    };
  }, [data, title, agentKey, subtitle]);

  return (
    <section className="dav" style={{ ["--accent" as string]: accent }}>
      <div className="dav-header">
        <div>
          <div className="dav-badge">
            <span className="dav-dot" /> {agentKey.toUpperCase()} AGENT
          </div>
          <h2 className="dav-title">{title}</h2>
          <p className="dav-sub">{subtitle}</p>
        </div>
        <div className="dav-header-right">
          {data && (
            <span className="dav-health" style={{ color: HEALTH_COLOR[data.health] ?? "#94a3b8" }}>
              ● {HEALTH_LABEL[data.health] ?? data.health}
              <span className="dav-conf">{Math.round((data.confidence ?? 0) * 100)}% conf</span>
            </span>
          )}
          <AgentReportingControls
            agentName={title}
            generateReportData={buildReportData}
            disabled={!data || loading}
          />
          <button className="dav-btn" onClick={load} disabled={loading}>
            {loading ? "Analysing…" : "Re-run Analysis"}
          </button>
        </div>
      </div>

      {headerExtra && <div className="dav-header-extra">{headerExtra}</div>}

      {error && (
        <div className="dav-error">
          <strong>{title} note:</strong> {error}
        </div>
      )}

      {data?.not_estimable_reason && (
        <div className="dav-empty">{data.not_estimable_reason}</div>
      )}

      {data && <p className="dav-summary">{data.summary}</p>}

      {!data && !loading && !error && (
        <div className="dav-empty">
          No analysis has been run in this session yet — press <strong>Re-run Analysis</strong> to compute live
          metrics, findings, and recommendations from the current data.
        </div>
      )}

      {/* Metrics */}
      {data && data.metrics.length > 0 && (
        <div className="dav-metrics">
          {data.metrics.map((m, i) => (
            <div key={i} className="dav-card">
              <p className="dav-card-label">{m.label}</p>
              <p className="dav-card-value">
                {fmt(m.value)}
                {m.unit ? <span className="dav-card-unit"> {m.unit}</span> : null}
              </p>
              {m.description && <p className="dav-card-desc">{m.description}</p>}
            </div>
          ))}
        </div>
      )}

      {/* Dynamic Cesium Route-Intelligence Feature (Logistics Agent Only) */}
      {agentKey === "logistics" && (
        <div className="dav-section dav-route-section">
          <div className="dav-route-header">
            <div>
              <div className="dav-badge">
                <span className="dav-dot" /> 3D ROUTE INTELLIGENCE
              </div>
              <h3 className="dav-section-title" style={{ margin: "6px 0 2px" }}>
                Order Shipment & Road Route Inspection
              </h3>
              <p className="dav-sub" style={{ fontSize: "12px", margin: 0 }}>
                Trace any real Olist order from its seller fulfillment warehouse to customer delivery address on the 3D globe with live road geometry.
              </p>
            </div>
            <div className="dav-route-quick-orders">
              <span className="dav-quick-label">Sample Orders:</span>
              {routeSamples.map((sample) => (
                <button
                  key={sample.order_id}
                  className="dav-quick-btn"
                  onClick={() => {
                    setOrderInput(sample.order_id);
                    fetchRoute(sample.order_id);
                  }}
                >
                  {`${sample.order_id.slice(0, 8)}… (${sample.label})`}
                </button>
              ))}
            </div>
          </div>

          <div className="dav-route-search-bar">
            <input
              className="dav-route-input"
              value={orderInput}
              onChange={(e) => setOrderInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && fetchRoute(orderInput)}
              placeholder="Enter a real Olist Order ID (or pick a sample above)..."
            />
            <button
              className="dav-btn dav-btn-primary"
              disabled={routeLoading || !orderInput.trim()}
              onClick={() => fetchRoute(orderInput)}
            >
              {routeLoading ? "Calculating Route…" : "Inspect Route"}
            </button>
          </div>

          {routeError && (
            <div className="dav-error" style={{ margin: "10px 0" }}>
              <strong>Route Error:</strong> {routeError}
            </div>
          )}

          <div className="dav-route-workspace">
            {/* Cesium 3D Globe Viewer - Always rendered to show interactive globe */}
            <div className="dav-route-map-panel">
              <CesiumRouteViewer
                origin={routeData?.origin || null}
                destination={routeData?.destination || null}
                geometry={routeData?.route?.geometry || []}
                distanceKm={routeData?.route?.distance_km}
                durationFormatted={routeData?.route?.duration_formatted}
                accentColor={accent}
              />
            </div>

            {/* Order Information & Logistics Intelligence Panel */}
            <div className="dav-route-info-panel">
              {routeData ? (
                <>
                  <div className="dav-info-card dav-info-highlight">
                    <div className="dav-info-row-between">
                      <div>
                        <span className="dav-info-sub">ORDER ID</span>
                        <h4 className="dav-info-oid">{routeData.order_id}</h4>
                      </div>
                      <span
                        className="dav-status-pill"
                        style={{
                          background:
                            routeData.order_status === "delivered"
                              ? "var(--status-success-bg)"
                              : "var(--status-warning-bg)",
                          color:
                            routeData.order_status === "delivered"
                              ? "var(--status-success)"
                              : "var(--status-warning)",
                        }}
                      >
                        {routeData.order_status.toUpperCase()}
                      </span>
                    </div>

                    {/* Multiple Sellers Selection */}
                    {routeData.has_multiple_sellers && (
                      <div className="dav-multi-seller-box">
                        <span className="dav-multi-seller-label">
                          ⚠️ MULTIPLE SELLERS ({routeData.sellers.length} Fulfillment Sources):
                        </span>
                        <div className="dav-seller-pills">
                          {routeData.sellers.map((s, idx) => (
                            <button
                              key={s.seller_id}
                              className={`dav-seller-pill ${routeData.selected_seller_id === s.seller_id ? "active" : ""}`}
                              onClick={() => fetchRoute(routeData.order_id, s.seller_id)}
                            >
                              Seller {idx + 1}: {s.city}, {s.state} ({s.item_count} items)
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Origin vs Destination Grid */}
                  <div className="dav-lane-grid">
                    <div className="dav-lane-box origin">
                      <span className="dav-lane-label">📦 ORIGIN (SELLER)</span>
                      <strong className="dav-lane-place">
                        {routeData.origin.city.toUpperCase()}, {routeData.origin.state}
                      </strong>
                      <span className="dav-lane-sub">
                        ZIP: {routeData.origin.zip_code_prefix} • ID: {routeData.origin.seller_id.slice(0, 10)}…
                      </span>
                    </div>
                    <div className="dav-lane-arrow">➔</div>
                    <div className="dav-lane-box destination">
                      <span className="dav-lane-label">🏠 DESTINATION (CUSTOMER)</span>
                      <strong className="dav-lane-place">
                        {routeData.destination.city.toUpperCase()}, {routeData.destination.state}
                      </strong>
                      <span className="dav-lane-sub">
                        ZIP: {routeData.destination.zip_code_prefix} • ID: {routeData.destination.customer_id.slice(0, 10)}…
                      </span>
                    </div>
                  </div>

                  {/* Road Route & Transit Metrics */}
                  <div className="dav-route-metrics-grid">
                    <div className="dav-metric-tile">
                      <span className="tile-label">Road Distance</span>
                      <strong className="tile-value">{routeData.route?.distance_km?.toLocaleString()} km</strong>
                      <span className="tile-sub">{routeData.route?.service}</span>
                    </div>
                    <div className="dav-metric-tile">
                      <span className="tile-label">Est. Drive Time</span>
                      <strong className="tile-value">{routeData.route?.duration_formatted || "—"}</strong>
                      <span className="tile-sub">Highway Transit</span>
                    </div>
                    <div className="dav-metric-tile">
                      <span className="tile-label">Freight Value</span>
                      <strong className="tile-value">
                        {new Intl.NumberFormat("en-US", { style: "currency", currency: "BRL" }).format(routeData.freight_value)}
                      </strong>
                      <span className="tile-sub">Total: {new Intl.NumberFormat("en-US", { style: "currency", currency: "BRL" }).format(routeData.total_value)}</span>
                    </div>
                    <div className="dav-metric-tile">
                      <span className="tile-label">Delivery SLA</span>
                      <strong
                        className="tile-value"
                        style={{
                          color: routeData.is_late ? "var(--status-danger)" : "var(--status-success)",
                        }}
                      >
                        {routeData.is_late ? `Delayed +${routeData.delivery_delay_days}d` : "On Time"}
                      </strong>
                      <span className="tile-sub">
                        Transit: {routeData.transit_days !== null ? `${routeData.transit_days} days` : "In flight"}
                      </span>
                    </div>
                  </div>

                  {/* Logistics Intelligence & Operational Risk */}
                  <div className="dav-logistics-risk-card">
                    <div className="dav-risk-head">
                      <span className="dav-risk-title">Operational Risk Assessment:</span>
                      <span
                        className="dav-sev"
                        style={{
                          background:
                            SEV_COLOR[routeData.logistics_intelligence?.risk_level] ||
                            "var(--status-neutral)",
                        }}
                      >
                        {routeData.logistics_intelligence?.risk_level}
                      </span>
                    </div>
                    <ul className="dav-risk-reasons">
                      {routeData.logistics_intelligence?.reasons?.map((reason, idx) => (
                        <li key={idx}>{reason}</li>
                      ))}
                    </ul>
                    {routeData.review_score && (
                      <div className="dav-review-line">
                        ⭐ <strong>Customer Review Score:</strong> {routeData.review_score} / 5
                        {routeData.review_comment ? ` — "${routeData.review_comment}"` : ""}
                      </div>
                    )}
                  </div>
                </>
              ) : (
                <div className="dav-route-placeholder">
                  <div className="dav-placeholder-icon">🌐</div>
                  <h4>Interactive 3D Road Navigation Ready</h4>
                  <p>
                    Enter an Order ID above or pick a sample order to trace the origin-to-destination road route, navigation metrics, and delivery SLA.
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      )}


      {/* Findings */}
      {data && (
        <div className="dav-section">
          <h3 className="dav-section-title">Findings</h3>
          {data.findings.length === 0 ? (
            <div className="dav-empty">No findings — all dimensions within empirical baseline.</div>
          ) : (
            <div className="dav-findings">
              {data.findings.map((f, i) => (
                <div key={i} className="dav-finding">
                  <div className="dav-finding-head">
                    <span className="dav-sev" style={{ background: SEV_COLOR[f.severity] ?? "#475569" }}>
                      {f.severity}
                    </span>
                    <span className="dav-finding-title">{f.title}</span>
                    <span className="dav-finding-cat">{f.category}</span>
                  </div>
                  <p className="dav-finding-what">{f.what_happened}</p>
                  <p className="dav-finding-why">
                    <strong>Why it matters:</strong> {f.why_it_matters}
                  </p>
                  <p className="dav-finding-action">
                    <strong>→ Action:</strong> {f.recommended_action}
                  </p>
                  {f.evidence && <p className="dav-finding-evidence">Evidence: {f.evidence}</p>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Recommendations */}
      {data && data.recommendations.length > 0 && (
        <div className="dav-section">
          <h3 className="dav-section-title">Recommendations</h3>
          <div className="dav-recs">
            {data.recommendations.map((r, i) => (
              <div key={i} className="dav-rec">
                <div className="dav-rec-head">
                  <span className="dav-rec-title">{r.title}</span>
                  {r.priority && <span className="dav-rec-prio">{r.priority}</span>}
                </div>
                <p className="dav-rec-detail">{r.detail}</p>
                {r.expected_impact && <p className="dav-rec-impact">Impact: {r.expected_impact}</p>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Chart tables */}
      {chartTables.map(([name, rows]) => (
        <div key={name} className="dav-section">
          <h3 className="dav-section-title">{humanizeKey(name)}</h3>
          <div className="dav-table-wrap">
            <table className="dav-table">
              <thead>
                <tr>
                  {Object.keys(rows[0]).map((k) => (
                    <th key={k}>{humanizeKey(k)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 12).map((row, ri) => (
                  <tr key={ri}>
                    {Object.entries(row).map(([k, cell]) => (
                      <td key={k}>{fmtCell(k, cell)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}

      {/* Chat */}
      <div className="dav-section">
        <h3 className="dav-section-title">Ask the {title.split(" ")[0]} Agent</h3>
        <div className="dav-chat" ref={chatRef}>
          {chat.length === 0 && (
            <div className="dav-chat-empty">
              <p>Ask a question about this domain.</p>
              <div className="dav-suggestions">
                {suggestions.map((s) => (
                  <button key={s} className="dav-suggestion" onClick={() => setInput(s)}>
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {chat.map((m, i) => (
            <div key={i} className={"dav-msg " + (m.role === "user" ? "dav-msg-user" : "dav-msg-agent")}>
              <div className="dav-msg-avatar" aria-hidden="true">{m.role === "user" ? "U" : agentKey.slice(0, 1).toUpperCase()}</div>
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
              <div className="dav-msg-avatar" aria-hidden="true">{agentKey.slice(0, 1).toUpperCase()}</div>
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
            placeholder={`Ask ${title}…`}
          />
          <button className="dav-btn dav-btn-primary" disabled={asking || !input.trim()} onClick={() => ask(input)}>
            {asking ? "…" : "Send"}
          </button>
        </div>
      </div>
    </section>
  );
}
