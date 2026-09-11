"use client";

import React, { useCallback, useEffect, useState } from "react";
import "./OrchestratorView.css";
import { API_ENDPOINTS } from "./config";

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
  HEALTHY: "#34d399",
  NEEDS_ATTENTION: "#fbbf24",
  CRITICAL: "#f87171",
  NOT_ESTIMABLE: "#94a3b8",
  ERROR: "#f87171",
};
const AGENT_ACCENT: Record<string, string> = {
  orders: "#3b82f6",
  inventory: "#10b981",
  customer: "#6366f1",
  logistics: "#f59e0b",
  pricing: "#ec4899",
  marketing: "#8b5cf6",
};

export default function OrchestratorView({ refreshKey }: { refreshKey?: number }) {
  const [data, setData] = useState<OrchResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [stale, setStale] = useState(false);

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

  // Expensive: trigger a fresh cross-domain sweep. Explicit user action only.
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

  useEffect(() => {
    loadLatest();
  }, [loadLatest, refreshKey]);

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
            <span className="orch-health" style={{ color: HEALTH_COLOR[data.overall_health] ?? "#94a3b8" }}>
              ● {data.overall_health.replace(/_/g, " ")}
              <span className="orch-conf">{Math.round((data.overall_confidence ?? 0) * 100)}% conf</span>
            </span>
          )}
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
            <div key={d.agent} className="orch-domain" style={{ ["--a" as string]: AGENT_ACCENT[d.agent] ?? "#64748b" }}>
              <div className="orch-domain-head">
                <span className="orch-domain-name">{d.display_name}</span>
                <span className="orch-domain-health" style={{ color: HEALTH_COLOR[d.health] ?? "#94a3b8" }}>
                  ● {d.health.replace(/_/g, " ")}
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
