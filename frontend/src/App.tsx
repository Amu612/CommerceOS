import React, { useCallback, useEffect, useRef, useState } from "react";
import OrdersAgentDashboard from "./OrdersAgentDashboard";
import InventoryAgentView from "./InventoryAgentView";
import CustomerAgentView from "./CustomerAgentView";
import DomainAgentView from "./DomainAgentView";
import OrchestratorView from "./OrchestratorView";
import ApprovalsView from "./ApprovalsView";
import Login from "./pages/Login";
import { useAuth } from "./auth/AuthContext";
import { subscribeEvents } from "./lib/ws";
import IngestionControlBar, { IngestionStatus } from "./IngestionControlBar";
import "./IngestionControlBar.css";
import { API_ENDPOINTS } from "./config";

type ActiveTab =
  | "orchestrator"
  | "orders"
  | "inventory"
  | "customer"
  | "logistics"
  | "pricing"
  | "marketing"
  | "approvals";

const TABS: { key: ActiveTab; label: string; badge: string; accent: string }[] = [
  { key: "orchestrator", label: "Orchestrator", badge: "All Agents", accent: "#ec4899" },
  { key: "orders", label: "Orders", badge: "Operations", accent: "#3b82f6" },
  { key: "inventory", label: "Inventory", badge: "Watchdog", accent: "#10b981" },
  { key: "logistics", label: "Logistics", badge: "Delivery", accent: "#f59e0b" },
  { key: "pricing", label: "Pricing", badge: "Margin", accent: "#ec4899" },
  { key: "marketing", label: "Marketing", badge: "Growth", accent: "#8b5cf6" },
  { key: "customer", label: "Customer", badge: "Support", accent: "#6366f1" },
  { key: "approvals", label: "Approvals", badge: "HITL", accent: "#fbbf24" },
];

function LlmBadge() {
  const [s, setS] = React.useState<{ resolved_provider?: string; chat_model_available?: boolean; reachable?: boolean | null; model?: string | null } | null>(null);
  React.useEffect(() => {
    const load = () =>
      fetch(API_ENDPOINTS.system.llm)
        .then((r) => (r.ok ? r.json() : null))
        .then(setS)
        .catch(() => setS(null));
    load();
    const t = window.setInterval(load, 15000);
    return () => window.clearInterval(t);
  }, []);
  const on = s?.chat_model_available && s?.reachable !== false;
  return (
    <span
      title={s ? `${s.resolved_provider} · ${s.model ?? "—"} · reachable: ${s.reachable}` : "checking…"}
      style={{
        fontSize: "10px",
        fontWeight: 700,
        textTransform: "uppercase",
        letterSpacing: "0.06em",
        padding: "3px 9px",
        borderRadius: "9999px",
        background: on ? "rgba(16,185,129,0.15)" : "rgba(148,163,184,0.12)",
        color: on ? "#6ee7b7" : "#94a3b8",
        border: on ? "1px solid rgba(16,185,129,0.35)" : "1px solid #334155",
        whiteSpace: "nowrap",
      }}
    >
      LLM: {on ? (s?.resolved_provider ?? "on") : "deterministic"}
    </span>
  );
}

const DARK_BG: Record<string, string> = {
  orchestrator: "#080d14",
  inventory: "#090d16",
  customer: "#0a0f1a",
  logistics: "#0a0f1a",
  pricing: "#0a0f1a",
  marketing: "#0a0f1a",
  approvals: "#0a0f1a",
};

export default function App() {
  const { user, authRequired, ready, logout } = useAuth();
  const [activeTab, setActiveTab] = useState<ActiveTab>("orchestrator");
  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<number | null>(null);

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
  // refreshKey drives EXPENSIVE re-analysis (agent /analyze, /orchestrator/run).
  // Bumped only by ingestion control + a slow streaming tick — never from WS,
  // because agent analysis itself emits events and that would loop.
  const [refreshKey, setRefreshKey] = useState(0);
  // liveKey drives CHEAP re-reads (approval queue, last persisted sweep).
  // Safe to bump on every inbound WS event.
  const [liveKey, setLiveKey] = useState(0);

  const fetchIngestionStatus = useCallback(async () => {
    try {
      const response = await fetch(API_ENDPOINTS.orders.ingestion.status);
      if (response.ok) setIngestion((await response.json()) as IngestionStatus);
    } catch {
      /* backend warming up */
    }
  }, []);

  const sendIngestionControl = async (action: string, speed?: number, step?: number) => {
    setIngestionLoading(true);
    try {
      const response = await fetch(API_ENDPOINTS.orders.ingestion.control, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, speed, step }),
      });
      if (response.ok) {
        const result = await response.json();
        if (result.ingestion) setIngestion(result.ingestion);
      }
      setRefreshKey((k) => k + 1);
    } catch (err) {
      console.error("Ingestion control failed:", err);
    } finally {
      setIngestionLoading(false);
    }
  };

  useEffect(() => {
    fetchIngestionStatus();
  }, [fetchIngestionStatus]);

  useEffect(() => {
    if (ingestion.status !== "running") return;
    // Poll the (cheap) ingestion status often for a smooth progress bar, but
    // only trigger the (expensive) agent re-analysis every ~12s.
    let ticks = 0;
    const timer = window.setInterval(async () => {
      await fetchIngestionStatus();
      ticks += 1;
      if (ticks % 6 === 0) setRefreshKey((k) => k + 1);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [ingestion.status, fetchIngestionStatus]);

  // Live push updates via WebSocket. Bumps only liveKey (cheap re-reads) and
  // shows a toast — it must never bump refreshKey or agent analysis would loop.
  useEffect(() => {
    const unsub = subscribeEvents((e) => {
      const type = String((e.data as any)?.type ?? "");
      const channel = String(e.channel ?? "");
      if (type === "connected" || type === "ping") return;
      const key = `${channel} ${type}`;
      if (!/orchestr|automation|approval|action|run_completed|sweep|notification/i.test(key)) return;
      setLiveKey((k) => k + 1);
      const label =
        /approval/i.test(key) ? "Approval queue updated" :
        /orchestr|sweep/i.test(key) ? "Orchestrator sweep completed" :
        /run_completed/i.test(key) ? `${(e.data as any)?.agent ?? "Agent"} analysis updated` :
        "Automation action updated";
      setToast(label);
      if (toastTimer.current) window.clearTimeout(toastTimer.current);
      toastTimer.current = window.setTimeout(() => setToast(null), 4000);
    });
    return unsub;
  }, []);

  if (ready && authRequired && !user) {
    return <Login />;
  }

  return (
    <div style={{ minHeight: "100vh", background: DARK_BG[activeTab] ?? "#f5f7fb" }}>
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: "12px",
          padding: "12px 24px",
          background: "#0b0f19",
          borderBottom: "1px solid #1e293b",
          position: "sticky",
          top: 0,
          zIndex: 50,
          boxShadow: "0 4px 12px rgba(0,0,0,0.25)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "14px" }}>
          <div
            style={{
              width: "34px",
              height: "34px",
              borderRadius: "10px",
              background: "linear-gradient(135deg, #3b82f6 0%, #ec4899 100%)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontWeight: 900,
              fontSize: "17px",
              color: "#fff",
              boxShadow: "0 0 16px rgba(59,130,246,0.4)",
            }}
          >
            ⚡
          </div>
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <span style={{ fontSize: "15px", fontWeight: 800, color: "#fff", letterSpacing: "-0.02em" }}>
                CommerceOS / Nexus
              </span>
              <span
                style={{
                  fontSize: "9px",
                  fontWeight: 700,
                  textTransform: "uppercase",
                  letterSpacing: "0.1em",
                  padding: "2px 7px",
                  borderRadius: "9999px",
                  background: "rgba(236,72,153,0.15)",
                  color: "#f9a8d4",
                  border: "1px solid rgba(236,72,153,0.3)",
                }}
              >
                Multi-Agent OS
              </span>
            </div>
            <p style={{ margin: 0, fontSize: "11px", color: "#64748b" }}>
              Autonomous e-commerce operations — 6 domain agents + orchestrator
            </p>
          </div>
          <LlmBadge />
        </div>

        <nav
          style={{
            display: "flex",
            alignItems: "center",
            gap: "4px",
            flexWrap: "wrap",
            background: "#060910",
            padding: "4px",
            borderRadius: "12px",
            border: "1px solid #1e293b",
          }}
        >
          {TABS.map((t) => {
            const active = activeTab === t.key;
            return (
              <button
                key={t.key}
                onClick={() => setActiveTab(t.key)}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "7px",
                  padding: "7px 13px",
                  borderRadius: "8px",
                  border: "none",
                  fontSize: "12.5px",
                  fontWeight: 700,
                  cursor: "pointer",
                  transition: "all 0.15s ease",
                  background: active ? t.accent : "transparent",
                  color: active ? "#06121f" : "#94a3b8",
                  boxShadow: active ? `0 2px 10px ${t.accent}55` : "none",
                }}
              >
                <span
                  style={{
                    width: "7px",
                    height: "7px",
                    borderRadius: "50%",
                    background: active ? "#06121f" : t.accent,
                    display: "inline-block",
                  }}
                />
                {t.label}
                <span
                  style={{
                    fontSize: "9px",
                    padding: "1px 5px",
                    borderRadius: "4px",
                    background: active ? "rgba(6,18,31,0.2)" : "#1e293b",
                    color: active ? "#06121f" : "#cbd5e1",
                  }}
                >
                  {t.badge}
                </span>
              </button>
            );
          })}
        </nav>

        {(user || authRequired) && (
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <span
              style={{
                fontSize: "11px",
                fontWeight: 700,
                color: "#cbd5e1",
                padding: "5px 10px",
                borderRadius: "9999px",
                background: "#0f172a",
                border: "1px solid #1e293b",
                whiteSpace: "nowrap",
              }}
            >
              {user ? `${user.username}${user.role ? ` · ${user.role}` : ""}` : "guest"}
            </span>
            {user && (
              <button
                onClick={logout}
                style={{
                  fontSize: "11px",
                  fontWeight: 700,
                  color: "#fca5a5",
                  padding: "5px 12px",
                  borderRadius: "8px",
                  background: "transparent",
                  border: "1px solid #7f1d1d",
                  cursor: "pointer",
                }}
              >
                Log out
              </button>
            )}
          </div>
        )}
      </header>

      {toast && (
        <div
          style={{
            position: "fixed",
            top: "76px",
            right: "24px",
            zIndex: 90,
            padding: "10px 16px",
            borderRadius: "10px",
            background: "rgba(16,185,129,0.16)",
            border: "1px solid rgba(16,185,129,0.4)",
            color: "#6ee7b7",
            fontSize: "12.5px",
            fontWeight: 700,
            boxShadow: "0 8px 24px rgba(0,0,0,0.35)",
          }}
        >
          ⟳ {toast}
        </div>
      )}

      <div style={{ background: "#0b0f19", borderBottom: "1px solid #1e293b", padding: "10px 24px" }}>
        <IngestionControlBar status={ingestion} loading={ingestionLoading} onControl={sendIngestionControl} />
      </div>

      <main>
        {activeTab === "orchestrator" && <OrchestratorView refreshKey={refreshKey + liveKey} />}

        {activeTab === "approvals" && <ApprovalsView refreshKey={refreshKey + liveKey} />}

        {activeTab === "orders" && (
          <OrdersAgentDashboard
            analysisUrl={API_ENDPOINTS.orders.analyze}
            queryUrl={API_ENDPOINTS.orders.query}
            ingestionUrl={API_ENDPOINTS.orders.ingestion.control.replace("/control", "")}
            refreshIntervalMs={60000}
            hideIngestionBar={true}
            refreshKey={refreshKey}
          />
        )}

        {activeTab === "inventory" && (
          <InventoryAgentView
            monitorUrl={API_ENDPOINTS.inventory.monitor}
            queryUrl={API_ENDPOINTS.inventory.query}
            refreshIntervalMs={60000}
            refreshKey={refreshKey}
          />
        )}

        {activeTab === "customer" && (
          <CustomerAgentView
            agentsUrl={API_ENDPOINTS.customer.agents}
            queryUrl={API_ENDPOINTS.customer.query}
            refreshKey={refreshKey}
          />
        )}

        {activeTab === "logistics" && (
          <DomainAgentView
            agentKey="logistics"
            title="Logistics Intelligence"
            subtitle="Carrier & lane performance, transit-time distributions, delivery SLA, and late-delivery risk — grounded on the same live Olist / DataCo shipment data."
            accent="#f59e0b"
            analyzeUrl={API_ENDPOINTS.logistics.analyze}
            queryUrl={API_ENDPOINTS.logistics.query}
            refreshKey={refreshKey}
            suggestions={[
              "Which carrier is slowest?",
              "Which lane has the widest SLA gap?",
              "What is our on-time delivery rate?",
              "How bad is late-delivery risk?",
            ]}
          />
        )}

        {activeTab === "pricing" && (
          <DomainAgentView
            agentKey="pricing"
            title="Pricing & Margin Intelligence"
            subtitle="Blended & order-level margin, loss-making order detection, discount leakage, and category price/freight positioning."
            accent="#ec4899"
            analyzeUrl={API_ENDPOINTS.pricing.analyze}
            queryUrl={API_ENDPOINTS.pricing.query}
            refreshKey={refreshKey}
            suggestions={[
              "Which segment has the worst margin?",
              "How many orders are loss-making?",
              "Where is discount leaking?",
              "Which category has the highest freight drag?",
            ]}
          />
        )}

        {activeTab === "marketing" && (
          <DomainAgentView
            agentKey="marketing"
            title="Marketing Intelligence"
            subtitle="RFM customer segmentation, repeat-purchase rate, category demand trends, and next-best-campaign recommendations."
            accent="#8b5cf6"
            analyzeUrl={API_ENDPOINTS.marketing.analyze}
            queryUrl={API_ENDPOINTS.marketing.query}
            refreshKey={refreshKey}
            suggestions={[
              "Break down my RFM segments",
              "What is the repeat-purchase rate?",
              "Which categories are driving demand?",
              "What campaign should I run next?",
            ]}
          />
        )}
      </main>
    </div>
  );
}
