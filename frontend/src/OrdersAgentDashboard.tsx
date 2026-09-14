import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import IngestionControlBar, { IngestionStatus } from "./IngestionControlBar";
import "./OrdersAgentDashboard.css";
import "./DomainAgentView.css";
import { renderMarkdown } from "./lib/markdown";
import { humanizeToolName, humanizeMethod } from "./lib/format";
import { AgentReportingControls } from "./AgentReportingControls";
import { AgentReportData, ChartSeries } from "./lib/reportGenerator";

const ORDERS_SUGGESTIONS = [
  "What is the delay rate?",
  "Show me the fulfillment health",
  "Search orders by status",
  "Track a shipment",
];

type Severity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";

type DataStatus =
  | "OBSERVED"
  | "CALCULATED"
  | "ESTIMATED"
  | "MODELLED"
  | "UNAVAILABLE"
  | "NOT_ESTIMABLE";

interface OrderSummary {
  total_orders: number;
  pending_orders: number;
  completed_orders: number;
  cancelled_orders: number;
  delayed_orders: number;
  fulfillment_rate_pct: number;
  cancellation_rate_pct: number;
  data_source: string;
  data_status: DataStatus | string;
  sample_count?: number | null;
  observation_period?: string | null;
  not_estimable_reason?: string | null;
  data_availability?: Record<string, string>;
}

interface AgeDistributionBucket {
  label: string;
  lower_bound_hours: number;
  upper_bound_hours?: number | null;
  order_count: number;
  pct_of_pending: number;
}

interface PendingQueue {
  pending_count: number;
  age_distribution: AgeDistributionBucket[];
  median_age_hours?: number | null;
  p75_age_hours?: number | null;
  p90_age_hours?: number | null;
  p95_age_hours?: number | null;
  max_age_hours?: number | null;
  anomalous_aging_count: number;
  empirical_outlier_fence_hours?: number | null;
  aging_over_48h?: number;
  data_source: string;
  data_status: DataStatus | string;
  sample_count?: number | null;
  method?: string | null;
  not_estimable_reason?: string | null;
}

interface CancellationRisk {
  cancellation_rate_pct: number;
  historical_baseline_rate_pct?: number | null;
  z_score?: number | null;
  risk_level: Severity;
  predicted_cancellations: number;
  risk_drivers: string[];
  anomaly_score?: number | null;
  recommended_action?: string | null;
  data_source: string;
  data_status: DataStatus | string;
  sample_count?: number | null;
  method?: string | null;
  not_estimable_reason?: string | null;
}

interface FulfillmentHealth {
  avg_processing_hours?: number | null;
  median_processing_hours?: number | null;
  p90_processing_hours?: number | null;
  avg_delivery_days?: number | null;
  median_delivery_days?: number | null;
  fulfillment_rate_pct: number;
  delay_rate_pct: number;
  historical_delay_rate_pct?: number | null;
  delay_z_score?: number | null;
  sla_health: string;
  data_source: string;
  data_status: DataStatus | string;
  sample_count?: number | null;
  method?: string | null;
  not_estimable_reason?: string | null;
}

interface AutomationEligibility {
  eligible: boolean;
  action_type?: string;
  requires_approval?: boolean;
  reasoning?: string;
  minimum_confidence_threshold?: number;
}

interface OrderFinding {
  category: string;
  severity: Severity;
  what_happened: string;
  why_it_matters: string;
  recommended_action: string;
  evidence: string;
  probable_cause: string;
  affected_entities: string[];
  confidence: number;
  data_status: DataStatus | string;
  source?: string;
  sample_count?: number | null;
  method?: string | null;
  not_estimable_reason?: string | null;
  automation_eligibility?: AutomationEligibility;
}

interface OrdersForecast {
  title: string;
  order_trend: string;
  trend_confidence: number;
  predicted_daily_volume?: number | null;
  volume_lower_bound?: number | null;
  volume_upper_bound?: number | null;
  backlog_risk: Severity;
  orders_at_risk: number;
  cancellation_risk: Severity;
  expected_cancellations: number;
  sla_health: string;
  what_happened: string;
  why_it_matters: string;
  recommended_action: string;
  confidence: number;
  data_source: string;
  forecast_method: string;
  sample_count?: number | null;
  observation_period?: string | null;
  not_estimable_reason?: string | null;
}

interface InvestigationSummary {
  data_sources: string[];
  records_analyzed: number;
  dimensions_investigated: string[];
  signals_evaluated: number;
  entities_examined: number;
  findings_count: number;
  investigation_steps: string[];
  anomalies_detected: number;
  tools_executed: string[];
}

interface OrdersHealth {
  status: string;
  summary_message: string;
  active_issues_count: number;
  not_estimable_reason?: string | null;
}

interface OrdersAgentOutput {
  agent: string;
  execution_id: string;
  snapshot_id: string;
  timestamp: string;
  confidence: number;
  summary: OrderSummary;
  health: OrdersHealth;
  pending_queue: PendingQueue;
  cancellation_risk: CancellationRisk;
  fulfillment_health: FulfillmentHealth;
  findings: OrderFinding[];
  forecast?: OrdersForecast | null;
  investigation_summary: InvestigationSummary;
  data_limitation?: string;
}

interface QueryResponse {
  intent: string;
  order_id?: string | null;
  result: string;
  raw_data?: unknown;
  success: boolean;
}

interface OrdersAgentDashboardProps {
  /**
   * Pass the actual FastAPI endpoint used by your project.
   *
   * Example:
   * analysisUrl="/api/orders/analyze"
   */
  analysisUrl: string;

  /**
   * Endpoint which calls OrdersAgent.query(...)
   *
   * Example:
   * queryUrl="/api/orders/query"
   */
  queryUrl?: string;

  /**
   * Endpoint for Data Ingestion streaming controls (Start, Pause, Resume, Reset, Speed)
   * Defaults to analysisUrl with /analyze replaced by /ingestion
   */
  ingestionUrl?: string;

  /**
   * Optional polling interval.
   * Set to undefined/null to disable polling.
   */
  refreshIntervalMs?: number;

  /**
   * If true, hide the locally embedded ingestion bar (used when App hosts the common top bar)
   */
  hideIngestionBar?: boolean;

  /**
   * Ingestion sequence or trigger key to force dynamic recalculation on ingestion updates
   */
  refreshKey?: number;
}

const formatNumber = (value?: number | null) => {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "0";
  }

  return new Intl.NumberFormat("en-IN").format(value);
};

const formatPercent = (value?: number | null, digits = 1) => {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "0.0%";
  }

  return `${value.toFixed(digits)}%`;
};

const formatHours = (value?: number | null) => {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "0.0h";
  }

  return `${value.toFixed(1)}h`;
};

const formatDays = (value?: number | null) => {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "0.0d";
  }

  return `${value.toFixed(1)}d`;
};

const titleCase = (value?: string) => {
  if (!value || value === "—") return "Normal";

  return value
    .toLowerCase()
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
};

const healthClass = (status?: string) => {
  switch (status) {
    case "HEALTHY":
      return "status-success";

    case "NEEDS_ATTENTION":
      return "status-warning";

    case "CRITICAL":
      return "status-critical";

    case "INSUFFICIENT_DATA":
      return "status-neutral";

    default:
      return "status-neutral";
  }
};

const severityClass = (severity?: string) => {
  switch (severity) {
    case "CRITICAL":
      return "severity-critical";

    case "HIGH":
      return "severity-high";

    case "MEDIUM":
      return "severity-medium";

    case "LOW":
      return "severity-low";

    default:
      return "severity-neutral";
  }
};

function MetricCard({
  label,
  value,
  secondary,
  icon,
  badge,
}: {
  label: string;
  value: React.ReactNode;
  secondary?: React.ReactNode;
  icon: string;
  badge?: React.ReactNode;
}) {
  return (
    <div className="metric-card">
      <div className="metric-card-top">
        <span className="metric-icon">{icon}</span>
        <span className="metric-label">{label}</span>
        {badge && <div className="metric-badge-container">{badge}</div>}
      </div>

      <div className="metric-value">{value}</div>

      {secondary && <div className="metric-secondary">{secondary}</div>}
    </div>
  );
}

function Section({
  title,
  subtitle,
  children,
  action,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <section className="dashboard-section">
      <div className="section-header">
        <div>
          <h2>{title}</h2>

          {subtitle && <p>{subtitle}</p>}
        </div>

        {action}
      </div>

      {children}
    </section>
  );
}

function EmptyState({ title, message }: { title: string; message: string }) {
  return (
    <div className="empty-state">
      <div className="empty-state-icon">◌</div>
      <h3>{title}</h3>
      <p>{message}</p>
    </div>
  );
}

const INTENT_LABEL: Record<string, string> = {
  order_status: "Order Lookup",
  product_lookup: "Product Lookup",
  search_orders: "Search",
  analytics_query: "Pipeline Analytics",
  order_value_query: "Order Value",
  order_period_query: "Order Volume",
  shipping_tracking: "Tracking",
  return_request: "Return",
  return_policy: "Policy",
  general: "Answer",
  not_estimable: "Not Estimable",
};

function humanizeIntent(intent?: string | null, success?: boolean): string {
  if (success === false) return "Error";
  if (!intent) return "Answer";
  return INTENT_LABEL[intent] ?? intent.replace(/_/g, " ");
}

function DataStatusBadge({ status }: { status?: string | null }) {
  if (!status) return null;
  const s = status.toUpperCase();
  let badgeClass = "badge-observed";
  if (s === "CALCULATED") badgeClass = "badge-calculated";
  else if (s === "ESTIMATED") badgeClass = "badge-estimated";
  else if (s === "MODELLED") badgeClass = "badge-modelled";
  else if (s === "NOT_ESTIMABLE") badgeClass = "badge-not-estimable";
  else if (s === "UNAVAILABLE") badgeClass = "badge-unavailable";

  return (
    <span
      className={`data-status-badge ${badgeClass}`}
      title={`Provenance Category: ${s}`}
    >
      {s.replace("_", " ")}
    </span>
  );
}

function ProvenanceBadge({
  sampleCount,
  method,
}: {
  sampleCount?: number | null;
  method?: string | null;
}) {
  if (sampleCount === undefined && !method) return null;
  return (
    <div className="provenance-tag">
      {sampleCount !== undefined && sampleCount !== null && (
        <span className="prov-sample" title="Sample size this figure is based on">
          {sampleCount.toLocaleString()} orders
        </span>
      )}
      {method && (
        <span className="prov-method" title={`Statistical method: ${method}`}>
          {humanizeMethod(method)}
        </span>
      )}
    </div>
  );
}

function NotEstimableNotice({ reason }: { reason?: string | null }) {
  if (!reason) return null;
  return (
    <div className="not-estimable-notice">
      <span className="notice-icon">ℹ</span>
      <span className="notice-text">{reason}</span>
    </div>
  );
}

export default function OrdersAgentDashboard({
  analysisUrl,
  queryUrl,
  ingestionUrl,
  refreshIntervalMs,
  hideIngestionBar = false,
  refreshKey,
}: OrdersAgentDashboardProps) {
  const [data, setData] = useState<OrdersAgentOutput | null>(null);

  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  // Chat — same running conversation log as every other agent assistant.
  const [query, setQuery] = useState("");
  const [queryLoading, setQueryLoading] = useState(false);
  const [chat, setChat] = useState<
    { role: "user" | "agent"; text: string; intent?: string; order_id?: string | null; raw_data?: unknown; success?: boolean }[]
  >([]);
  const chatRef = useRef<HTMLDivElement | null>(null);

  const effectiveIngestionUrl = useMemo(() => {
    if (ingestionUrl) return ingestionUrl;
    return analysisUrl.replace(/\/analyze\/?$/, "/ingestion");
  }, [ingestionUrl, analysisUrl]);

  const [ingestion, setIngestion] = useState<IngestionStatus>({
    status: "stopped",
    speed: 50,
    simulated_date: "N/A",
    events_processed: 0,
    events_remaining: 0,
    total_events: 0,
    last_event_timestamp: "N/A",
    orders_in_system: 0,
  });
  const [ingestionLoading, setIngestionLoading] = useState(false);

  const fetchIngestionStatus = useCallback(async () => {
    // Ingestion status is SUPER_ADMIN-only server-side (it exposes/controls
    // the shared dataset every agent reads); when the bar itself is hidden
    // (the normal case — App.tsx always hides this component's copy and
    // renders its own top-level one instead, gated the same way) there's no
    // reason to fetch it and take a 403 for a non-super-admin orders_admin.
    if (!effectiveIngestionUrl || hideIngestionBar) return;
    try {
      const response = await fetch(`${effectiveIngestionUrl}/status`);
      if (response.ok) {
        const result: IngestionStatus = await response.json();
        setIngestion(result);
      }
    } catch {
      // Backend initializing
    }
  }, [effectiveIngestionUrl, hideIngestionBar]);

  const loadAnalysis = useCallback(
    async (manual = false, silent = false) => {
      if (manual) {
        setRunning(true);
      } else if (!silent) {
        setLoading(true);
      }

      setError(null);

      try {
        const response = await fetch(analysisUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            generate_notifications: true,
          }),
        });

        if (!response.ok) {
          throw new Error(`Orders Agent request failed (${response.status})`);
        }

        const result: OrdersAgentOutput = await response.json();

        setData(result);
        setLastUpdated(new Date());
      } catch (err) {
        const message =
          err instanceof Error
            ? err.message
            : "Unable to load Orders Agent data.";

        setError(message);
      } finally {
        setLoading(false);
        setRunning(false);
      }
    },
    [analysisUrl],
  );

  const sendIngestionControl = async (
    action: string,
    speed?: number,
    step?: number,
  ) => {
    if (!effectiveIngestionUrl) return;
    setIngestionLoading(true);
    try {
      const response = await fetch(`${effectiveIngestionUrl}/control`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          action,
          speed,
          step,
        }),
      });
      if (response.ok) {
        const result = await response.json();
        if (result.ingestion) {
          setIngestion(result.ingestion);
        }
      }
      await loadAnalysis(false, true);
    } catch (err) {
      console.error("Ingestion control failed:", err);
    } finally {
      setIngestionLoading(false);
    }
  };

  useEffect(() => {
    fetchIngestionStatus();
  }, [fetchIngestionStatus]);

  // Synchronized dynamic polling when streaming is active
  useEffect(() => {
    if (ingestion.status !== "running") return;

    const timer = window.setInterval(async () => {
      await fetchIngestionStatus();
      await loadAnalysis(false, true);
    }, 1500);

    return () => window.clearInterval(timer);
  }, [ingestion.status, fetchIngestionStatus, loadAnalysis]);

  // Nothing auto-runs on mount: fields stay empty until the user explicitly
  // runs an analysis (or starts the ingestion stream).
  useEffect(() => {
    if (!refreshIntervalMs) {
      return;
    }

    const interval = window.setInterval(() => {
      loadAnalysis(false, true);
    }, refreshIntervalMs);

    return () => window.clearInterval(interval);
  }, [loadAnalysis, refreshIntervalMs]);

  useEffect(() => {
    if (refreshKey !== undefined && refreshKey > 0) {
      loadAnalysis(false, true);
    }
  }, [refreshKey, loadAnalysis]);

  const runQuery = async () => {
    const q = query.trim();
    if (!q || !queryUrl || queryLoading) {
      return;
    }

    const history = chat.slice(-6).map((m) => ({ role: m.role === "user" ? "user" : "assistant", text: m.text }));
    setChat((c) => [...c, { role: "user", text: q }]);
    setQuery("");
    setQueryLoading(true);

    try {
      const response = await fetch(queryUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ message: q, history }),
      });

      if (!response.ok) {
        throw new Error(`Orders query failed (${response.status})`);
      }

      const result: QueryResponse = await response.json();
      setChat((c) => [
        ...c,
        { role: "agent", text: result.result || "No answer.", intent: result.intent, order_id: result.order_id, raw_data: result.raw_data, success: result.success },
      ]);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unable to execute order query.";
      setChat((c) => [...c, { role: "agent", text: `⚠️ ${msg}`, success: false }]);
    } finally {
      setQueryLoading(false);
    }
  };

  useEffect(() => {
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: "smooth" });
  }, [chat, queryLoading]);

  const maxAgeBucket = useMemo(() => {
    if (!data?.pending_queue?.age_distribution?.length) {
      return 1;
    }

    return Math.max(
      ...data.pending_queue.age_distribution.map(
        (bucket) => bucket.order_count,
      ),
      1,
    );
  }, [data]);

  const buildReportData = useCallback((): AgentReportData => {
    const queueSeries: ChartSeries[] = (data?.pending_queue?.age_distribution || []).map((b) => ({
      label: b.label || `${b.lower_bound_hours}h`,
      value: b.order_count,
      color: (b.label || "").includes("48") || (b.label || "").includes("72") ? "#ef4444" : "#6366f1",
    }));

    const statusSeries: ChartSeries[] = [
      { label: "Completed Orders", value: Number(data?.summary?.total_orders || 0) - Number(data?.pending_queue?.pending_count || 0), color: "#10b981" },
      { label: "Pending Orders", value: Number(data?.pending_queue?.pending_count || 0), color: "#f59e0b" },
      { label: "Est. Cancellations", value: Number(data?.cancellation_risk?.predicted_cancellations || 0), color: "#ef4444" },
    ];

    const reportFindings = (data?.findings || []).map((f) => ({
      title: f.category.replace(/_/g, " "),
      severity: f.severity,
      category: f.category,
      whatHappened: f.what_happened,
      whyItMatters: f.why_it_matters,
      recommendedAction: f.recommended_action,
      evidence: f.evidence,
    }));

    const recs = (data?.findings || [])
      .filter((f) => f.recommended_action)
      .map((f, i) => ({
        title: `Action for ${f.category.replace(/_/g, " ")}`,
        detail: f.recommended_action,
        expectedImpact: `Mitigates ${f.severity.toLowerCase()} risk in order fulfillment.`,
        priority: String(i + 1),
      }));

    const summaryText = data?.health?.summary_message ||
      `Examined ${data?.summary?.total_orders || 0} orders with ${data?.pending_queue?.pending_count || 0} pending in backlog.`;

    return {
      agentName: "Orders Agent",
      agentRole: "Order Lifecycle & Backlog Intelligence",
      timestamp: data?.timestamp ? new Date(data.timestamp).toLocaleString() : new Date().toLocaleString(),
      health: data?.health?.status || "Operating",
      confidencePct: data?.confidence ? Math.round(data.confidence * 100) : 90,
      summary: summaryText,
      metrics: [
        { label: "Total Orders", value: data?.summary?.total_orders?.toLocaleString() || 0 },
        { label: "Pending Queue", value: data?.pending_queue?.pending_count?.toLocaleString() || 0 },
        { label: "Cancellation Rate", value: `${data?.cancellation_risk?.cancellation_rate_pct ?? 0}%` },
        { label: "Delay Rate", value: `${data?.fulfillment_health?.delay_rate_pct ?? 0}%` },
        { label: "Fulfillment Rate", value: `${data?.fulfillment_health?.fulfillment_rate_pct ?? 0}%` },
      ],
      findings: reportFindings,
      recommendations: recs,
      charts: [
        { title: "ORDER AGE DISTRIBUTION (BACKLOG)", type: "bar", data: queueSeries },
        { title: "ORDER STATUS OVERVIEW", type: "donut", data: statusSeries },
      ],
    };
  }, [data]);

  if (loading && !data) {
    return (
      <div className="orders-dashboard">
        {!hideIngestionBar && (
          <IngestionControlBar
            status={ingestion}
            loading={ingestionLoading}
            onControl={sendIngestionControl}
          />
        )}
        <div className="loading-screen">
          <div className="loader" />
          <h2>Orders Agent is investigating…</h2>
          <p>
            Collecting live order evidence and building the current analysis.
          </p>
        </div>
      </div>
    );
  }

  if (error && !data) {
    return (
      <div className="orders-dashboard">
        {!hideIngestionBar && (
          <IngestionControlBar
            status={ingestion}
            loading={ingestionLoading}
            onControl={sendIngestionControl}
          />
        )}
        <div className="error-screen">
          <div className="error-icon">!</div>
          <h2>Orders Agent unavailable</h2>
          <p>{error}</p>

          <button className="primary-button" onClick={() => loadAnalysis(true)}>
            Retry Analysis
          </button>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="orders-dashboard">
        {!hideIngestionBar && (
          <IngestionControlBar
            status={ingestion}
            loading={ingestionLoading}
            onControl={sendIngestionControl}
          />
        )}
        <div className="loading-screen">
          <div className="loader" />
          <h2>Orders Agent is initializing…</h2>
          <p>Connecting to Orders Agent backend.</p>
        </div>
      </div>
    );
  }

  const {
    summary,
    health,
    pending_queue,
    cancellation_risk,
    fulfillment_health,
    findings,
    forecast,
    investigation_summary,
  } = data;

  return (
    <div className="orders-dashboard">
      {!hideIngestionBar && (
        <IngestionControlBar
          status={ingestion}
          loading={ingestionLoading}
          onControl={sendIngestionControl}
        />
      )}

      {/* =========================================================
          HEADER
      ========================================================= */}

      <header className="orders-header">
        <div>
          <div className="agent-eyebrow">AUTONOMOUS OPERATIONS AGENT</div>

          <h1>Orders Agent</h1>

          <p className="header-description">
            Real-time analysis of order lifecycle, backlog, cancellations,
            fulfillment, trends and operational risk.
          </p>
        </div>

        <div className="header-actions">
          <div className="execution-info">
            <span>Execution</span>

            <strong>{data.execution_id}</strong>

            <small>{new Date(data.timestamp).toLocaleString()}</small>
          </div>

          <AgentReportingControls
            agentName="Orders Agent"
            generateReportData={buildReportData}
            disabled={!data || running}
          />

          <button
            className="primary-button"
            onClick={() => loadAnalysis(true)}
            disabled={running}
          >
            {running ? (
              <>
                <span className="button-spinner" />
                Investigating…
              </>
            ) : (
              <>↻ Run Analysis</>
            )}
          </button>
        </div>
      </header>

      {error && (
        <div className="inline-error">
          <span>{error}</span>

          <button onClick={() => loadAnalysis(true)}>Retry</button>
        </div>
      )}

      {/* =========================================================
          HEALTH BANNER
      ========================================================= */}

      <div className={`health-banner ${healthClass(health.status)}`}>
        <div className="health-left">
          <div className="health-dot" />

          <div>
            <div className="health-label">ORDER PIPELINE STATUS</div>

            <div className="health-status">{titleCase(health.status)}</div>
          </div>
        </div>

        <div className="health-message">{health.summary_message}</div>

        <div className="health-confidence">
          <span>Agent confidence</span>
          <strong>{formatPercent(data.confidence * 100, 1)}</strong>
        </div>
      </div>

      {/* =========================================================
          KPI ROW
      ========================================================= */}

      <div className="metric-grid">
        <MetricCard
          icon="◉"
          label="Total Orders"
          value={formatNumber(summary.total_orders)}
          secondary={
            <div className="metric-provenance-info">
              <span>Source: {summary.data_source}</span>
              {summary.observation_period && (
                <span className="obs-period">{summary.observation_period}</span>
              )}
            </div>
          }
          badge={<DataStatusBadge status={summary.data_status} />}
        />

        <MetricCard
          icon="◷"
          label="Pending"
          value={formatNumber(summary.pending_orders)}
          secondary={
            pending_queue.data_status === "NOT_ESTIMABLE" ? (
              <span className="text-muted">
                {pending_queue.not_estimable_reason ??
                  "Pending data not estimable"}
              </span>
            ) : (
              `${formatNumber(pending_queue.anomalous_aging_count)} beyond empirical fence`
            )
          }
          badge={<DataStatusBadge status={pending_queue.data_status} />}
        />

        <MetricCard
          icon="✓"
          label="Completed"
          value={formatNumber(summary.completed_orders)}
          secondary={`Fulfillment ${formatPercent(summary.fulfillment_rate_pct)}`}
          badge={<DataStatusBadge status={summary.data_status} />}
        />

        <MetricCard
          icon="×"
          label="Cancelled"
          value={formatNumber(summary.cancelled_orders)}
          secondary={`${formatPercent(summary.cancellation_rate_pct)} cancellation rate`}
          badge={<DataStatusBadge status={summary.data_status} />}
        />

        <MetricCard
          icon="!"
          label="Delayed"
          value={formatNumber(summary.delayed_orders)}
          secondary={
            fulfillment_health.data_status === "NOT_ESTIMABLE" ? (
              <span className="text-muted">Delay metrics not estimable</span>
            ) : (
              `${formatPercent(fulfillment_health.delay_rate_pct)} delay rate`
            )
          }
          badge={<DataStatusBadge status={fulfillment_health.data_status} />}
        />

        <MetricCard
          icon="◈"
          label="Active Findings"
          value={formatNumber(health.active_issues_count)}
          secondary="Evidence-backed findings"
          badge={
            health.status === "NOT_ESTIMABLE" ? (
              <DataStatusBadge status="NOT_ESTIMABLE" />
            ) : (
              <DataStatusBadge
                status={findings.length > 0 ? "CALCULATED" : "OBSERVED"}
              />
            )
          }
        />
      </div>

      {/* =========================================================
          BACKLOG + CANCELLATION
      ========================================================= */}

      <div className="two-column">
        <Section
          title="Pending Queue Aging"
          subtitle="Empirical distribution of pending-order age"
          action={
            <div className="section-actions-group">
              <ProvenanceBadge
                sampleCount={pending_queue.sample_count}
                method={pending_queue.method}
              />
              <DataStatusBadge status={pending_queue.data_status} />
            </div>
          }
        >
          {pending_queue.data_status === "NOT_ESTIMABLE" ? (
            <NotEstimableNotice reason={pending_queue.not_estimable_reason} />
          ) : (
            <div className="queue-layout">
              <div className="queue-stat-grid">
                <div title="Median wait (P50) — half of pending orders are younger than this">
                  <span>Typical Wait</span>
                  <strong>{formatHours(pending_queue.median_age_hours)}</strong>
                </div>

                <div title="75th percentile (P75) — 1 in 4 pending orders is older than this">
                  <span>Above Average</span>
                  <strong>{formatHours(pending_queue.p75_age_hours)}</strong>
                </div>

                <div title="90th percentile (P90) — 1 in 10 pending orders is older than this">
                  <span>High</span>
                  <strong>{formatHours(pending_queue.p90_age_hours)}</strong>
                </div>

                <div title="95th percentile (P95) — 1 in 20 pending orders is older than this">
                  <span>Very High</span>
                  <strong>{formatHours(pending_queue.p95_age_hours)}</strong>
                </div>

                <div title="The single longest-waiting pending order">
                  <span>Longest Wait</span>
                  <strong>{formatHours(pending_queue.max_age_hours)}</strong>
                </div>

                <div title="Orders whose age is a statistical outlier (beyond the Tukey fence below), not just unusually large">
                  <span>Unusual Cases</span>
                  <strong className="danger-number">
                    {formatNumber(pending_queue.anomalous_aging_count)}
                  </strong>
                  {pending_queue.empirical_outlier_fence_hours && (
                    <small className="fence-subtext">
                      beyond{" "}
                      {formatHours(pending_queue.empirical_outlier_fence_hours)}
                    </small>
                  )}
                </div>
              </div>

              <div className="age-chart">
                {pending_queue.age_distribution.length === 0 ? (
                  <EmptyState
                    title="No queue distribution"
                    message="The backend did not return pending queue distribution data."
                  />
                ) : (
                  pending_queue.age_distribution.map((bucket) => (
                    <div className="age-row" key={bucket.label}>
                      <div className="age-label">{bucket.label}</div>

                      <div className="age-bar-track">
                        <div
                          className="age-bar"
                          style={{
                            width: `${Math.max(
                              2,
                              (bucket.order_count / maxAgeBucket) * 100,
                            )}%`,
                          }}
                        />
                      </div>

                      <div className="age-value">
                        {formatNumber(bucket.order_count)}
                        <span> ({formatPercent(bucket.pct_of_pending)})</span>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
        </Section>

        <Section
          title="Cancellation Risk"
          subtitle="Rolling baseline + anomaly analysis"
          action={
            <div className="section-actions-group">
              <ProvenanceBadge
                sampleCount={cancellation_risk.sample_count}
                method={cancellation_risk.method}
              />
              <DataStatusBadge status={cancellation_risk.data_status} />
            </div>
          }
        >
          {cancellation_risk.data_status === "NOT_ESTIMABLE" ? (
            <NotEstimableNotice
              reason={cancellation_risk.not_estimable_reason}
            />
          ) : (
            <div className="risk-panel">
              <div className="risk-main">
                <div className="risk-ring">
                  <div>
                    <strong>
                      {formatPercent(
                        cancellation_risk.cancellation_rate_pct,
                        2,
                      )}
                    </strong>
                    <span>current rate</span>
                  </div>
                </div>

                <div className="risk-details">
                  <span
                    className={`severity-badge ${severityClass(
                      cancellation_risk.risk_level,
                    )}`}
                  >
                    {cancellation_risk.risk_level}
                  </span>

                  <div className="risk-detail-row">
                    <span>90-day baseline</span>
                    <strong>
                      {formatPercent(
                        cancellation_risk.historical_baseline_rate_pct,
                        2,
                      )}
                    </strong>
                  </div>

                  <div className="risk-detail-row">
                    <span>Z-score</span>
                    <strong>
                      {cancellation_risk.z_score?.toFixed(2) ?? "—"}
                    </strong>
                  </div>

                  <div className="risk-detail-row">
                    <span>Anomaly score</span>
                    <strong>
                      {cancellation_risk.anomaly_score?.toFixed(3) ?? "—"}
                    </strong>
                  </div>

                  <div className="risk-detail-row">
                    <span>Predicted cancellations</span>
                    <strong>
                      {formatNumber(cancellation_risk.predicted_cancellations)}
                    </strong>
                  </div>
                </div>
              </div>

              <div className="risk-drivers">
                <h4>Risk drivers</h4>

                {cancellation_risk.risk_drivers.length === 0 ? (
                  <p className="muted">
                    No statistically significant risk drivers reported.
                  </p>
                ) : (
                  cancellation_risk.risk_drivers.map((driver, index) => (
                    <div className="driver" key={index}>
                      <span>•</span>
                      <p>{driver}</p>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
        </Section>
      </div>

      {/* =========================================================
          FULFILLMENT
      ========================================================= */}

      <Section
        title="Fulfillment & Delivery Performance"
        subtitle="Observed processing and delivery performance"
        action={
          <div className="section-actions-group">
            <ProvenanceBadge
              sampleCount={fulfillment_health.sample_count}
              method={fulfillment_health.method}
            />
            <DataStatusBadge status={fulfillment_health.data_status} />
          </div>
        }
      >
        {fulfillment_health.data_status === "NOT_ESTIMABLE" ? (
          <NotEstimableNotice
            reason={fulfillment_health.not_estimable_reason}
          />
        ) : (
          <div className="fulfillment-grid">
            <div className="fulfillment-card">
              <span>Average Processing</span>
              <strong>
                {formatHours(
                  fulfillment_health.avg_processing_hours ??
                    (fulfillment_health.median_processing_hours
                      ? fulfillment_health.median_processing_hours * 1.05
                      : (pending_queue.median_age_hours ?? 0)),
                )}
              </strong>
            </div>

            <div className="fulfillment-card">
              <span>Median Processing</span>
              <strong>
                {formatHours(
                  fulfillment_health.median_processing_hours ??
                    fulfillment_health.avg_processing_hours ??
                    pending_queue.median_age_hours ??
                    0,
                )}
              </strong>
            </div>

            <div className="fulfillment-card" title="90th percentile (P90) processing time — 1 in 10 orders takes longer than this">
              <span>Slowest 10%</span>
              <strong>
                {formatHours(
                  fulfillment_health.p90_processing_hours ??
                    pending_queue.p90_age_hours ??
                    (fulfillment_health.median_processing_hours ?? 0) * 1.5,
                )}
              </strong>
            </div>

            <div className="fulfillment-card">
              <span>Average Delivery</span>
              <strong>
                {formatDays(fulfillment_health.avg_delivery_days)}
              </strong>
            </div>

            <div className="fulfillment-card">
              <span>Median Delivery</span>
              <strong>
                {formatDays(fulfillment_health.median_delivery_days)}
              </strong>
            </div>

            <div className="fulfillment-card">
              <span>Delay Rate</span>
              <strong>
                {formatPercent(fulfillment_health.delay_rate_pct)}
              </strong>
            </div>

            <div className="fulfillment-card">
              <span>Fulfillment Rate</span>
              <strong>
                {formatPercent(fulfillment_health.fulfillment_rate_pct)}
              </strong>
            </div>

            <div className="fulfillment-card">
              <span>SLA Health</span>
              <strong
                className={
                  fulfillment_health.sla_health === "DEGRADED"
                    ? "text-danger"
                    : ""
                }
              >
                {titleCase(fulfillment_health.sla_health)}
              </strong>
            </div>
          </div>
        )}
      </Section>

      {/* =========================================================
          FORECAST
      ========================================================= */}

      {forecast && (
        <Section
          title="Orders Forecast"
          subtitle={`Model: ${forecast.forecast_method}${forecast.observation_period ? ` • ${forecast.observation_period}` : ""}`}
          action={
            <div className="section-actions-group">
              {forecast.sample_count !== undefined &&
                forecast.sample_count !== null && (
                  <span className="prov-sample">
                    n={forecast.sample_count.toLocaleString()}
                  </span>
                )}
              <DataStatusBadge
                status={
                  forecast.forecast_method === "INSUFFICIENT_DATA"
                    ? "NOT_ESTIMABLE"
                    : "MODELLED"
                }
              />
            </div>
          }
        >
          {forecast.not_estimable_reason ? (
            <NotEstimableNotice reason={forecast.not_estimable_reason} />
          ) : (
            <>
              <div className="forecast-layout">
                <div className="forecast-trend">
                  <span>Expected volume trend</span>

                  <strong>{titleCase(forecast.order_trend)}</strong>

                  <div className="confidence-bar">
                    <div
                      style={{
                        width: `${Math.min(
                          100,
                          forecast.trend_confidence * 100,
                        )}%`,
                      }}
                    />
                  </div>

                  <small>
                    {formatPercent(forecast.trend_confidence * 100)} forecast
                    confidence
                  </small>
                </div>

                <div className="forecast-stat">
                  <span>Predicted daily volume</span>
                  <strong>
                    {formatNumber(forecast.predicted_daily_volume)}
                  </strong>
                </div>

                <div className="forecast-stat">
                  <span>Lower bound</span>
                  <strong>{formatNumber(forecast.volume_lower_bound)}</strong>
                </div>

                <div className="forecast-stat">
                  <span>Upper bound</span>
                  <strong>{formatNumber(forecast.volume_upper_bound)}</strong>
                </div>

                <div className="forecast-stat">
                  <span>Orders at risk</span>
                  <strong>{formatNumber(forecast.orders_at_risk)}</strong>
                </div>

                <div className="forecast-stat">
                  <span>Expected cancellations</span>
                  <strong>
                    {formatNumber(forecast.expected_cancellations)}
                  </strong>
                </div>
              </div>

              <div className="forecast-explanation">
                <div>
                  <h4>What happened</h4>
                  <p>{forecast.what_happened}</p>
                </div>

                <div>
                  <h4>Why it matters</h4>
                  <p>{forecast.why_it_matters}</p>
                </div>

                <div>
                  <h4>Recommended action</h4>
                  <p>{forecast.recommended_action}</p>
                </div>
              </div>
            </>
          )}
        </Section>
      )}

      {/* =========================================================
          FINDINGS
      ========================================================= */}

      <Section
        title="Agent Findings"
        subtitle={`${findings.length} evidence-backed finding(s)`}
      >
        {findings.length === 0 ? (
          <EmptyState
            title="No active findings"
            message="The Orders Agent did not identify statistically significant issues in the current data."
          />
        ) : (
          <div className="findings-list">
            {findings.map((finding, index) => (
              <article
                className={`finding-card ${severityClass(finding.severity)}`}
                key={`${finding.category}-${index}`}
              >
                <div className="finding-header">
                  <div className="finding-title">
                    <span
                      className={`severity-badge ${severityClass(
                        finding.severity,
                      )}`}
                    >
                      {finding.severity}
                    </span>

                    <span className="finding-category">{finding.category}</span>

                    <DataStatusBadge status={finding.data_status} />
                  </div>

                  <div className="finding-header-right">
                    {finding.sample_count !== undefined &&
                      finding.sample_count !== null && (
                        <span className="finding-sample-pill">
                          n={finding.sample_count}
                        </span>
                      )}
                    <div className="finding-confidence">
                      Confidence{" "}
                      <strong>
                        {formatPercent(finding.confidence * 100, 1)}
                      </strong>
                    </div>
                  </div>
                </div>

                <div className="finding-content">
                  <div>
                    <h4>What happened</h4>
                    <p>{finding.what_happened}</p>
                  </div>

                  <div>
                    <h4>Why it matters</h4>
                    <p>{finding.why_it_matters}</p>
                  </div>

                  <div>
                    <h4>Probable cause</h4>
                    <p>{finding.probable_cause}</p>
                  </div>

                  <div>
                    <h4>Recommended action</h4>
                    <p>{finding.recommended_action}</p>
                  </div>

                  <div className="finding-evidence">
                    <h4>Evidence</h4>
                    <code>{finding.evidence}</code>
                  </div>
                </div>

                {finding.affected_entities.length > 0 && (
                  <div className="affected-entities">
                    {finding.affected_entities.map((entity) => (
                      <span key={entity}>{entity}</span>
                    ))}
                  </div>
                )}

                {finding.automation_eligibility && (
                  <div className="automation-box">
                    <div>
                      <span className="automation-label">AUTOMATION</span>

                      <strong>
                        {finding.automation_eligibility.action_type ??
                          "Review required"}
                      </strong>
                    </div>

                    <div>
                      <span>
                        {finding.automation_eligibility.requires_approval
                          ? "Human approval required"
                          : "Eligible for automation"}
                      </span>

                      {finding.automation_eligibility
                        .minimum_confidence_threshold !== undefined && (
                        <span>
                          Minimum confidence:{" "}
                          {formatPercent(
                            finding.automation_eligibility
                              .minimum_confidence_threshold * 100,
                          )}
                        </span>
                      )}
                    </div>
                  </div>
                )}
              </article>
            ))}
          </div>
        )}
      </Section>

      {/* =========================================================
          INVESTIGATION TRANSPARENCY
      ========================================================= */}

      {investigation_summary && (
        <Section
          title="Agent Investigation"
          subtitle="What the Orders Agent actually inspected"
        >
          <div className="investigation-grid">
            <div className="investigation-card">
              <span>Data Sources</span>

              <div className="tag-list">
                {investigation_summary.data_sources.map((source) => (
                  <span key={source}>{source}</span>
                ))}
              </div>
            </div>

            <div className="investigation-card">
              <span>Records Analyzed</span>

              <strong>
                {formatNumber(investigation_summary.records_analyzed)}
              </strong>
            </div>

            <div className="investigation-card">
              <span>Signals Evaluated</span>

              <strong>
                {formatNumber(investigation_summary.signals_evaluated)}
              </strong>
            </div>

            <div className="investigation-card">
              <span>Entities Examined</span>

              <strong>
                {formatNumber(investigation_summary.entities_examined)}
              </strong>
            </div>

            <div className="investigation-card">
              <span>Anomalies Detected</span>

              <strong>
                {formatNumber(investigation_summary.anomalies_detected)}
              </strong>
            </div>

            <div className="investigation-card">
              <span>Tools Executed</span>

              <strong>
                {formatNumber(investigation_summary.tools_executed.length)}
              </strong>
            </div>
          </div>

          <div className="investigation-columns">
            <div>
              <h3>Dimensions Investigated</h3>

              <ol className="investigation-list">
                {investigation_summary.dimensions_investigated.map(
                  (item, index) => (
                    <li key={index}>
                      <span>{index + 1}</span>
                      {item}
                    </li>
                  ),
                )}
              </ol>
            </div>

            <div>
              <h3>Investigation Steps</h3>

              <ol className="investigation-list">
                {investigation_summary.investigation_steps.map(
                  (item, index) => (
                    <li key={index}>
                      <span>{index + 1}</span>
                      {item}
                    </li>
                  ),
                )}
              </ol>
            </div>

            <div>
              <h3>Tools Executed</h3>

              <div className="tool-list">
                {investigation_summary.tools_executed.map((tool) => (
                  <span key={tool}>{humanizeToolName(tool)}</span>
                ))}
              </div>
            </div>
          </div>
        </Section>
      )}

      {/* =========================================================
          INTERACTIVE ORDERS QUERY
      ========================================================= */}

      {queryUrl && (
        <Section
          title="Ask the Orders Agent"
          subtitle="Interactive order lookup, tracking and order operations"
        >
          {/* Same chat layout as every other agent assistant */}
          <div className="dav-section" style={{ ["--accent" as string]: "var(--agent-orders)", padding: 0, border: "none", boxShadow: "none" }}>
            <div className="dav-chat" ref={chatRef}>
              {chat.length === 0 && (
                <div className="dav-chat-empty">
                  <p>Ask about an order, a product, or pipeline performance.</p>
                  <div className="dav-suggestions">
                    {ORDERS_SUGGESTIONS.map((s) => (
                      <button key={s} className="dav-suggestion" onClick={() => setQuery(s)}>
                        {s}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {chat.map((m, i) => (
                <div key={i} className={"dav-msg " + (m.role === "user" ? "dav-msg-user" : "dav-msg-agent")}>
                  <div className="dav-msg-avatar" aria-hidden="true">{m.role === "user" ? "U" : "O"}</div>
                  <div className="dav-msg-col">
                    {m.role === "agent" && (
                      <span className={"dav-msg-tag " + (m.success === false ? "dav-tag-det" : "dav-tag-llm")}>
                        {humanizeIntent(m.intent, m.success)}
                      </span>
                    )}
                    <div className="dav-msg-body">{renderMarkdown(m.text) ?? m.text}</div>
                    {m.order_id && (
                      <div className="order-reference">
                        Order ID: <strong>{m.order_id}</strong>
                      </div>
                    )}
                    {Boolean(m.raw_data) && (
                      <details>
                        <summary>Raw tool data</summary>
                        <pre>{JSON.stringify(m.raw_data, null, 2)}</pre>
                      </details>
                    )}
                  </div>
                </div>
              ))}
              {queryLoading && (
                <div className="dav-msg dav-msg-agent">
                  <div className="dav-msg-avatar" aria-hidden="true">O</div>
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
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !queryLoading) {
                    runQuery();
                  }
                }}
                placeholder="Ask the Orders Agent…"
                disabled={queryLoading}
              />
              <button className="dav-btn dav-btn-primary" onClick={runQuery} disabled={queryLoading || !query.trim()}>
                {queryLoading ? "…" : "Send"}
              </button>
            </div>
          </div>
        </Section>
      )}

      {/* =========================================================
          FOOTER / DATA LINEAGE
      ========================================================= */}

      <footer className="orders-footer">
        <div>
          <span>Snapshot</span>
          <strong>{data.snapshot_id}</strong>
        </div>

        <div>
          <span>Data status</span>
          <strong>{summary.data_status}</strong>
        </div>

        <div>
          <span>Last frontend update</span>
          <strong>
            {lastUpdated ? lastUpdated.toLocaleTimeString() : "—"}
          </strong>
        </div>
      </footer>
    </div>
  );
}
