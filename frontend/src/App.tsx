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
import CompetitorFeedControl from "./CompetitorFeedControl";
import "./App.css";
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
  {
    key: "orchestrator",
    label: "Orchestrator",
    badge: "All Agents",
    accent: "var(--agent-orchestrator)",
  },
  { key: "orders", label: "Orders", badge: "Operations", accent: "var(--agent-orders)" },
  { key: "inventory", label: "Inventory", badge: "Watchdog", accent: "var(--agent-inventory)" },
  { key: "logistics", label: "Logistics", badge: "Delivery", accent: "var(--agent-logistics)" },
  { key: "pricing", label: "Pricing", badge: "Margin", accent: "var(--agent-pricing)" },
  { key: "marketing", label: "Marketing", badge: "Growth", accent: "var(--agent-marketing)" },
  { key: "customer", label: "Customer", badge: "Support", accent: "var(--agent-customer)" },
  { key: "approvals", label: "Approvals", badge: "HITL", accent: "var(--agent-approvals)" },
];

/** Mounts its children once `mounted` first goes true, then keeps them mounted
 * (hidden via CSS when not `show`) instead of unmounting on every tab switch. */
function TabSlot({
  show,
  mounted,
  children,
}: {
  show: boolean;
  mounted: boolean;
  children: React.ReactNode;
}) {
  if (!mounted) return null;
  return <div style={{ display: show ? undefined : "none" }}>{children}</div>;
}

function LlmBadge() {
  const [s, setS] = React.useState<{
    resolved_provider?: string;
    chat_model_available?: boolean;
    reachable?: boolean | null;
    model?: string | null;
  } | null>(null);
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
      className={"app-llm-badge " + (on ? "on" : "off")}
      title={
        s ? `${s.resolved_provider} · ${s.model ?? "—"} · reachable: ${s.reachable}` : "checking…"
      }
    >
      LLM: {on ? (s?.resolved_provider ?? "on") : "deterministic"}
    </span>
  );
}

/**
 * What this session's user may see, straight from the server (`/auth/me` /
 * the login response — see `app.core.rbac` on the backend, the single
 * source of truth both sides read from). When auth isn't enforced at all
 * (`user` is null but the app still renders, per the check below) everyone
 * gets the full nav — that's the existing local-dev/demo convenience, not a
 * client-side permission decision.
 */
function visibleTabs(user: ReturnType<typeof useAuth>["user"]): ActiveTab[] {
  if (!user) return TABS.map((t) => t.key);
  const allowed = new Set<ActiveTab>(user.permitted_agents as ActiveTab[]);
  const tabs: ActiveTab[] = [];
  if (user.can_access_orchestrator) tabs.push("orchestrator");
  tabs.push(...TABS.map((t) => t.key).filter((k) => allowed.has(k)));
  tabs.push("approvals"); // every role gets Approvals — server-side scoped to their own domain
  return tabs;
}

export default function App() {
  const { user, authRequired, ready, logout } = useAuth();
  const [activeTab, setActiveTabState] = useState<ActiveTab>("orchestrator");
  // Tabs render lazily on first visit, then stay mounted (just hidden) rather
  // than unmounting on every switch — otherwise each view's chat history and
  // already-fetched analysis were thrown away every time you left the tab,
  // which read as "the previous agent's data got wiped".
  const [visitedTabs, setVisitedTabs] = useState<Set<ActiveTab>>(() => new Set(["orchestrator"]));
  const [toast, setToast] = useState<string | null>(null);
  const toastTimer = useRef<number | null>(null);

  const permitted = visibleTabs(user);

  // A click always goes through this — even if a nav button somehow rendered
  // for a tab the current role can't see, `goToTab` itself still refuses it.
  // The API calls each view makes are the real boundary (403 either way);
  // this just keeps the client's own state from ever pointing at a view the
  // user has no business seeing rendered.
  const goToTab = (key: ActiveTab) => {
    if (!permitted.includes(key)) return;
    setActiveTabState(key);
    setVisitedTabs((prev) => (prev.has(key) ? prev : new Set(prev).add(key)));
  };

  // A tab only ever renders visible when it's both the active one AND still
  // permitted — the second half is what stops a stale `activeTab` (set
  // before `permitted` caught up, e.g. right after logging in as a more
  // restricted role in the same tab) from flashing a view its role can't
  // reach.
  const showTab = (key: ActiveTab) => activeTab === key && permitted.includes(key);

  // When the session (and so the permitted set) changes — login, logout, or
  // a role swap between accounts in the same tab — land on a tab this user
  // can actually see instead of leaving them stuck on (or defaulting to) one
  // they can't, which for most domain admins is "orchestrator".
  useEffect(() => {
    if (permitted.includes(activeTab)) return;
    const next = permitted[0] ?? "approvals";
    setActiveTabState(next);
    setVisitedTabs((prev) => (prev.has(next) ? prev : new Set(prev).add(next)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id]);

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

  // Ingestion status is SUPER_ADMIN-only server-side (see the bar's own
  // comment above) — skip polling it at all for anyone else instead of
  // generating a 403 on every tick.
  const canControlIngestion = !user || user.is_super_admin;

  const fetchIngestionStatus = useCallback(async () => {
    if (!canControlIngestion) return;
    try {
      const response = await fetch(API_ENDPOINTS.orders.ingestion.status);
      if (response.ok) setIngestion((await response.json()) as IngestionStatus);
    } catch {
      /* backend warming up */
    }
  }, [canControlIngestion]);

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
      if (!/orchestr|automation|approval|action|run_completed|sweep|notification/i.test(key))
        return;
      setLiveKey((k) => k + 1);
      const label = /approval/i.test(key)
        ? "Approval queue updated"
        : /orchestr|sweep/i.test(key)
          ? "Orchestrator sweep completed"
          : /run_completed/i.test(key)
            ? `${(e.data as any)?.agent ?? "Agent"} analysis updated`
            : "Automation action updated";
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
    <div className="app-shell">
      <header className="app-header">
        <div className="app-brand-row">
          <div className="app-logo">⚡</div>
          <div>
            <div className="app-title-row">
              <span className="app-title">CommerceOS</span>
              <span className="app-badge">Multi-Agent OS</span>
            </div>
            <p className="app-tagline">
              Autonomous e-commerce operations — 6 domain agents + orchestrator
            </p>
          </div>
          <LlmBadge />
        </div>

        <nav className="app-nav">
          {TABS.filter((t) => permitted.includes(t.key)).map((t) => {
            const active = activeTab === t.key;
            return (
              <button
                key={t.key}
                onClick={() => goToTab(t.key)}
                className={"app-tab" + (active ? " active" : "")}
                style={
                  active
                    ? { background: t.accent, boxShadow: `0 2px 10px ${t.accent}55` }
                    : undefined
                }
              >
                <span
                  className="app-tab-dot"
                  style={{ background: active ? "var(--text-on-accent)" : t.accent }}
                />
                {t.label}
                <span className="app-tab-badge">{t.badge}</span>
              </button>
            );
          })}
        </nav>

        {(user || authRequired) && (
          <div className="app-user-row">
            <span className="app-user-chip">
              {user ? `${user.username}${user.role ? ` · ${user.role}` : ""}` : "guest"}
            </span>
            {user && (
              <button onClick={logout} className="app-logout-btn">
                Log out
              </button>
            )}
          </div>
        )}
      </header>

      {toast && <div className="app-toast">⟳ {toast}</div>}

      {/* Ingestion/replay control mutates the one shared dataset every agent
          reads (and "Reset" can wipe it outright), so it's SUPER_ADMIN-only
          server-side too (see `ingestion_dependency` on the backend) — a
          domain admin who could still see this bar would just get a 403 on
          every button, so it's hidden rather than shown-disabled. */}
      {canControlIngestion && (
        <div className="app-ingestion-bar">
          <IngestionControlBar
            status={ingestion}
            loading={ingestionLoading}
            onControl={sendIngestionControl}
          />
        </div>
      )}

      <main>
        {/* Defense in depth, not the real boundary — every view's own API
            calls are what actually enforce this (403 either way). This just
            means a stale/manipulated `activeTab` renders an explicit refusal
            instead of silently falling through to a view the click-through
            nav already wouldn't offer. */}
        {!permitted.includes(activeTab) && (
          <div className="app-forbidden">
            <h2>Access Restricted</h2>
            <p>
              Your role ({user?.role ?? "guest"}) doesn't have access to this view. Redirecting you
              to somewhere you do…
            </p>
          </div>
        )}

        {/* Each tab mounts the first time it's visited, then stays mounted
            (hidden, not destroyed) so its chat history and already-fetched
            analysis survive switching to another tab and back. */}
        <TabSlot show={showTab("orchestrator")} mounted={visitedTabs.has("orchestrator")}>
          <OrchestratorView refreshKey={refreshKey + liveKey} />
        </TabSlot>

        <TabSlot show={showTab("approvals")} mounted={visitedTabs.has("approvals")}>
          <ApprovalsView refreshKey={refreshKey + liveKey} />
        </TabSlot>

        <TabSlot show={showTab("orders")} mounted={visitedTabs.has("orders")}>
          <OrdersAgentDashboard
            analysisUrl={API_ENDPOINTS.orders.analyze}
            queryUrl={API_ENDPOINTS.orders.query}
            ingestionUrl={API_ENDPOINTS.orders.ingestion.control.replace("/control", "")}
            refreshIntervalMs={60000}
            hideIngestionBar={true}
            refreshKey={refreshKey}
          />
        </TabSlot>

        <TabSlot show={showTab("inventory")} mounted={visitedTabs.has("inventory")}>
          <InventoryAgentView
            monitorUrl={API_ENDPOINTS.inventory.monitor}
            queryUrl={API_ENDPOINTS.inventory.query}
            refreshIntervalMs={60000}
            refreshKey={refreshKey}
          />
        </TabSlot>

        <TabSlot show={showTab("customer")} mounted={visitedTabs.has("customer")}>
          <CustomerAgentView
            agentsUrl={API_ENDPOINTS.customer.agents}
            queryUrl={API_ENDPOINTS.customer.query}
            refreshKey={refreshKey}
          />
        </TabSlot>

        <TabSlot show={showTab("logistics")} mounted={visitedTabs.has("logistics")}>
          <DomainAgentView
            agentKey="logistics"
            title="Logistics Intelligence"
            subtitle="Carrier & lane performance, transit-time distributions, delivery SLA, and late-delivery risk — grounded on the same live Olist / DataCo shipment data."
            accent="var(--agent-logistics)"
            analyzeUrl={API_ENDPOINTS.logistics.analyze}
            queryUrl={API_ENDPOINTS.logistics.query}
            routeUrl={API_ENDPOINTS.logistics.route}
            refreshKey={refreshKey}
            suggestions={[
              "Which carrier is slowest?",
              "Which lane has the widest SLA gap?",
              "What is our on-time delivery rate?",
              "How bad is late-delivery risk?",
            ]}
          />
        </TabSlot>

        <TabSlot show={showTab("pricing")} mounted={visitedTabs.has("pricing")}>
          <DomainAgentView
            agentKey="pricing"
            title="Pricing & Margin Intelligence"
            subtitle="Blended & order-level margin, loss-making order detection, discount leakage, and category price/freight positioning."
            accent="var(--agent-pricing)"
            analyzeUrl={API_ENDPOINTS.pricing.analyze}
            queryUrl={API_ENDPOINTS.pricing.query}
            refreshKey={refreshKey}
            suggestions={[
              "Which segment has the worst margin?",
              "How many orders are loss-making?",
              "Where is discount leaking?",
              "Which category has the highest freight drag?",
            ]}
            headerExtra={<CompetitorFeedControl />}
          />
        </TabSlot>

        <TabSlot show={showTab("marketing")} mounted={visitedTabs.has("marketing")}>
          <DomainAgentView
            agentKey="marketing"
            title="Marketing Intelligence"
            subtitle="RFM customer segmentation, repeat-purchase rate, category demand trends, and next-best-campaign recommendations."
            accent="var(--agent-marketing)"
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
        </TabSlot>
      </main>
    </div>
  );
}
