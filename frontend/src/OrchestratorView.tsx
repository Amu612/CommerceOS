"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import "./OrchestratorView.css";
import { API_ENDPOINTS } from "./config";
import { AgentReportingControls } from "./AgentReportingControls";
import { AgentReportData, ChartSeries } from "./lib/reportGenerator";

type DomainFinding = { agent: string; category: string; severity: string; title: string; recommended_action: string; confidence: number };
type DomainSnapshot = {
  agent: string;
  display_name: string;
  health: string;
  headline: string;
  confidence: number;
  metrics: { label: string; value: unknown }[];
  findings: DomainFinding[];
  latency_ms: number;
  error?: string | null;
};
type SystemicFinding = { title: string; severity: string; domains: string[]; explanation: string; recommended_action: string };
type Conflict = { between: string[]; description: string; resolution: string };
type OrchResult = {
  execution_id: string;
  timestamp: string;
  overall_health: string;
  overall_confidence: number;
  summary: string;
  domains: DomainSnapshot[];
  systemic_findings: SystemicFinding[];
  conflicts: Conflict[];
  priority_actions: string[];
  kpis: Record<string, unknown>;
  llm_backed: boolean;
};

const HEALTH_COLOR: Record<string, string> = {
  HEALTHY: "#0f766e",
  NEEDS_ATTENTION: "#b45309",
  CRITICAL: "#b91c1c",
  NOT_ESTIMABLE: "#857f93",
  ERROR: "#b91c1c",
};
const HEALTH_LABEL: Record<string, string> = {
  HEALTHY: "Healthy",
  NEEDS_ATTENTION: "Needs Attention",
  CRITICAL: "Critical",
  NOT_ESTIMABLE: "Not Estimable",
  ERROR: "Error",
};
const AGENT_ACCENT: Record<string, string> = {
  orders: "#1d4ed8",
  inventory: "#0f766e",
  customer: "#3730a3",
  logistics: "#a16207",
  pricing: "#9d174d",
  marketing: "#6d28d9",
};

export default function OrchestratorView({ refreshKey }: { refreshKey?: number }) {
  const [data, setData] = useState<OrchResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [stale, setStale] = useState(false);
  // Guards the one-time auto-sweep below so it never fires more than once per
  // mount — repeatedly auto-triggering the expensive sweep would reintroduce
  // the WS-event -> refresh -> re-sweep loop this view deliberately avoids.

  // Cheap: read the last persisted sweep. Used on mount and on every refresh tick.
  const loadLatest = useCallback(async () => {
    setError(null);
    try {
      const r = await fetch(API_ENDPOINTS.orchestrator.latest);
      if (!r.ok) throw new Error(`Orchestrator returned ${r.status}`);
      const d = (await r.json()) as OrchResult & { stale?: boolean };
      setData(d);
      setStale(Boolean(d.stale));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load orchestrator");
    } finally {
      setLoading(false);
    }
  }, []);

  // Expensive: trigger a fresh cross-domain sweep. Explicit user action, or
  // the one-time auto-refresh above when the cached sweep looks stale/empty.
  const runSweep = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(API_ENDPOINTS.orchestrator.run, { method: "POST" });
      if (!r.ok) throw new Error(`Orchestrator returned ${r.status}`);
      setData((await r.json()) as OrchResult);
      setStale(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to run orchestrator");
    } finally {
      setLoading(false);
    }
  }, []);

  // Nothing auto-runs on mount: the sweep stays empty until the user explicitly
  // runs one (or a WS/refresh event bumps refreshKey after a real run).
  const mountedRef = useRef(false);
  useEffect(() => {
    if (!mountedRef.current) {
      mountedRef.current = true;
      setLoading(false);
      if (refreshKey !== undefined && refreshKey > 0) loadLatest();
      return;
    }
    loadLatest();
  }, [loadLatest, refreshKey]);

  const buildReportData = useCallback((): AgentReportData => {
    const domainHealthSeries: ChartSeries[] = [];
    if (data?.domains) {
      const counts: Record<string, number> = {};
      data.domains.forEach((d) => {
        const h = HEALTH_LABEL[d.health] || d.health || "Unknown";
        counts[h] = (counts[h] || 0) + 1;
      });
      Object.entries(counts).forEach(([h, count]) => {
        domainHealthSeries.push({
          label: h,
          value: count,
          color: h === "Healthy" ? "#10b981" : h === "Needs Attention" ? "#f59e0b" : "#ef4444",
        });
      });
    }

    const findingSeveritySeries: ChartSeries[] = [
      { label: "Critical", value: Number(data?.kpis["critical_findings"] || 0), color: "#ef4444" },
      { label: "Open Issues", value: Math.max(0, Number(data?.kpis["open_findings"] || 0) - Number(data?.kpis["critical_findings"] || 0)), color: "#f59e0b" },
      { label: "Systemic Patterns", value: data?.systemic_findings?.length || 0, color: "#6366f1" },
    ];

    const reportFindings = [
      ...(data?.systemic_findings || []).map((sf) => ({
        title: sf.title,
        severity: sf.severity || "HIGH",
        category: `Systemic (${sf.domains.join(", ")})`,
        whatHappened: sf.explanation,
        whyItMatters: "This issue touches multiple business areas simultaneously and needs cross-team cooperation.",
        recommendedAction: sf.recommended_action,
      })),
      ...(data?.conflicts || []).map((c) => ({
        title: `Conflicting goals between ${c.between.join(" and ")}`,
        severity: "MEDIUM",
        category: "Cross-Agent Conflict",
        whatHappened: c.description,
        whyItMatters: "When teams pull in different directions, business efficiency and profits suffer.",
        recommendedAction: c.resolution,
      })),
    ];

    return {
      agentName: "Nexus Orchestrator",
      agentRole: "Cross-Domain Command Center",
      timestamp: data?.timestamp ? new Date(data.timestamp).toLocaleString() : new Date().toLocaleString(),
      health: data?.overall_health ? HEALTH_LABEL[data.overall_health] || data.overall_health : "Healthy",
      confidencePct: data?.overall_confidence ? Math.round(data.overall_confidence * 100) : 90,
      summary: data?.summary || "Cross-domain system sweep completed successfully.",
      metrics: [
        { label: "Domains Healthy", value: `${String(data?.kpis?.["domains_healthy"] ?? 0)} / ${String(data?.kpis?.["domains_total"] ?? 6)}` },
        { label: "Open Findings", value: Number(data?.kpis?.["open_findings"] ?? 0) },
        { label: "Critical Findings", value: Number(data?.kpis?.["critical_findings"] ?? 0) },
        { label: "Systemic Patterns", value: data?.systemic_findings?.length ?? 0 },
        { label: "Conflicts Resolved", value: data?.conflicts?.length ?? 0 },
      ],
      findings: reportFindings,
      recommendations: (data?.priority_actions || []).map((actionStr: string, i: number) => ({
        title: `Priority Action #${i + 1}`,
        detail: actionStr,
        expectedImpact: "Resolves prioritized cross-domain conflict or bottlenecks.",
        priority: String(i + 1),
      })),
      charts: [
        { title: "DOMAIN AGENT HEALTH BREAKDOWN", type: "donut", data: domainHealthSeries },
        { title: "SYSTEMIC FINDINGS & ISSUES", type: "bar", data: findingSeveritySeries },
      ],
    };
  }, [data]);

  return (
    <section className="orch">
      <div className="orch-header">
        <div>
          <div className="orch-badge">
            <span className="orch-dot" /> NEXUS ORCHESTRATOR
          </div>
          <h2 className="orch-title">Cross-Domain Command Center</h2>
          <p className="orch-sub">
            One coordinated sweep of all six domain agents — health, findings, systemic patterns, and
            conflicting recommendations resolved into a single priority list.
          </p>
        </div>
        <div className="orch-header-right">
          {data && (
            <span className="orch-health" style={{ color: HEALTH_COLOR[data.overall_health] ?? "#857f93" }}>
              ● {HEALTH_LABEL[data.overall_health] ?? data.overall_health}
              <span className="orch-conf">{Math.round((data.overall_confidence ?? 0) * 100)}% conf</span>
            </span>
          )}
          <AgentReportingControls
            agentName="Nexus Orchestrator"
            generateReportData={buildReportData}
            disabled={!data || loading}
          />
          <button className="orch-btn" onClick={runSweep} disabled={loading}>
            {loading ? "Coordinating…" : stale ? "Run Sweep" : "Re-run Sweep"}
          </button>
        </div>
      </div>

      {error && (
        <div className="orch-error">
          <strong>Orchestrator note:</strong> {error}
        </div>
      )}

      {data && <p className="orch-summary">{data.summary}</p>}

      {!data && !loading && !error && (
        <div className="dav-empty">
          No cross-domain sweep has been run in this session yet — press <strong>Run Sweep</strong> to execute all
          six domain agents and compute the coordinated view.
        </div>
      )}

      {/* KPI strip */}
      {data && (
        <div className="orch-kpis">
          <Kpi label="Domains Healthy" value={`${data.kpis["domains_healthy"] ?? 0} / ${data.kpis["domains_total"] ?? 6}`} />
          <Kpi label="Open Findings" value={String(data.kpis["open_findings"] ?? 0)} />
          <Kpi label="Critical" value={String(data.kpis["critical_findings"] ?? 0)} tone="crit" />
          <Kpi label="Systemic Patterns" value={String(data.systemic_findings.length)} tone="warn" />
          <Kpi label="Conflicts Resolved" value={String(data.conflicts.length)} />
        </div>
      )}

      {/* Domain grid */}
      {data && (
        <div className="orch-grid">
          {data.domains.map((d) => (
            <div key={d.agent} className="orch-domain" style={{ ["--a" as string]: AGENT_ACCENT[d.agent] ?? "#857f93" }}>
              <div className="orch-domain-head">
                <span className="orch-domain-name">{d.display_name}</span>
                <span className="orch-domain-health" style={{ color: HEALTH_COLOR[d.health] ?? "#857f93" }}>
                  ● {HEALTH_LABEL[d.health] ?? d.health}
                </span>
              </div>
              <p className="orch-domain-headline">{d.headline}</p>
              <div className="orch-domain-metrics">
                {d.metrics.slice(0, 4).map((m, i) => (
                  <div key={i} className="orch-mini">
                    <span className="orch-mini-label">{m.label}</span>
                    <span className="orch-mini-value">{String(m.value)}</span>
                  </div>
                ))}
              </div>
              {d.findings.length > 0 && (
                <div className="orch-domain-findings">
                  {d.findings.slice(0, 3).map((f, i) => (
                    <div key={i} className="orch-df">
                      <span className="orch-df-sev" data-sev={f.severity}>
                        {f.severity}
                      </span>
                      {f.title}
                    </div>
                  ))}
                </div>
              )}
              <span className="orch-latency">{Math.round(d.latency_ms)}ms</span>
            </div>
          ))}
        </div>
      )}

      {/* Systemic findings */}
      {data && data.systemic_findings.length > 0 && (
        <div className="orch-section">
          <h3 className="orch-section-title">Systemic Cross-Domain Findings</h3>
          {data.systemic_findings.map((s, i) => (
            <div key={i} className="orch-systemic">
              <div className="orch-systemic-head">
                <span className="orch-df-sev" data-sev={s.severity}>
                  {s.severity}
                </span>
                <span className="orch-systemic-title">{s.title}</span>
                <span className="orch-systemic-domains">{s.domains.join(" · ")}</span>
              </div>
              <p className="orch-systemic-exp">{s.explanation}</p>
              <p className="orch-systemic-act">
                <strong>→ Coordinated action:</strong> {s.recommended_action}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* Conflicts */}
      {data && data.conflicts.length > 0 && (
        <div className="orch-section">
          <h3 className="orch-section-title">Conflict Resolution</h3>
          {data.conflicts.map((c, i) => (
            <div key={i} className="orch-conflict">
              <div className="orch-conflict-head">{c.between.join("  ⟷  ")}</div>
              <p className="orch-conflict-desc">{c.description}</p>
              <p className="orch-conflict-res">
                <strong>Resolution:</strong> {c.resolution}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* Priority actions */}
      {data && data.priority_actions.length > 0 && (
        <div className="orch-section">
          <h3 className="orch-section-title">Priority Action Queue</h3>
          <ol className="orch-actions">
            {data.priority_actions.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ol>
        </div>
      )}
    </section>
  );
}

function Kpi({ label, value, tone }: { label: string; value: string; tone?: "crit" | "warn" }) {
  return (
    <div className={"orch-kpi" + (tone ? ` orch-kpi-${tone}` : "")}>
      <span className="orch-kpi-value">{value}</span>
      <span className="orch-kpi-label">{label}</span>
    </div>
  );
}
