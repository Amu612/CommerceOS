"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "./DomainAgentView.css";

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
  refreshKey?: number;
  suggestions?: string[];
};

const HEALTH_COLOR: Record<string, string> = {
  HEALTHY: "#34d399",
  NEEDS_ATTENTION: "#fbbf24",
  CRITICAL: "#f87171",
  NOT_ESTIMABLE: "#94a3b8",
};
const SEV_COLOR: Record<string, string> = {
  CRITICAL: "#f87171",
  HIGH: "#fb923c",
  MEDIUM: "#fbbf24",
  LOW: "#60a5fa",
};

function fmt(v: unknown): string {
  if (typeof v === "number") return new Intl.NumberFormat().format(v);
  return String(v ?? "—");
}

export default function DomainAgentView({
  agentKey,
  title,
  subtitle,
  accent,
  analyzeUrl,
  queryUrl,
  refreshKey,
  suggestions = [],
}: DomainAgentViewProps) {
  const [data, setData] = useState<AnalysisOutput | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  useEffect(() => {
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: "smooth" });
  }, [chat, asking]);

  const ask = async (raw: string) => {
    const q = raw.trim();
    if (!q || asking) return;
    setChat((c) => [...c, { role: "user", text: q }]);
    setInput("");
    setAsking(true);
    try {
      const r = await fetch(queryUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q }),
      });
      const j = await r.json();
      setChat((c) => [...c, { role: "agent", text: j.answer || "No answer.", llm: j.llm_backed }]);
    } catch {
      setChat((c) => [...c, { role: "agent", text: "⚠️ Request failed." }]);
    } finally {
      setAsking(false);
    }
  };

  const charts = data?.charts ?? {};
  const chartTables = useMemo(
    () =>
      Object.entries(charts).filter(
        ([, v]) => Array.isArray(v) && v.length > 0 && typeof (v as unknown[])[0] === "object",
      ) as [string, Record<string, unknown>[]][],
    [charts],
  );

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
              ● {data.health.replace(/_/g, " ")}
              <span className="dav-conf">{Math.round((data.confidence ?? 0) * 100)}% conf</span>
            </span>
          )}
          <button className="dav-btn" onClick={load} disabled={loading}>
            {loading ? "Analysing…" : "Re-run Analysis"}
          </button>
        </div>
      </div>

      {error && (
        <div className="dav-error">
          <strong>{title} note:</strong> {error}
        </div>
      )}

      {data?.not_estimable_reason && (
        <div className="dav-empty">{data.not_estimable_reason}</div>
      )}

      {data && <p className="dav-summary">{data.summary}</p>}

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
          <h3 className="dav-section-title">{name.replace(/_/g, " ")}</h3>
          <div className="dav-table-wrap">
            <table className="dav-table">
              <thead>
                <tr>
                  {Object.keys(rows[0]).map((k) => (
                    <th key={k}>{k.replace(/_/g, " ")}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 12).map((row, ri) => (
                  <tr key={ri}>
                    {Object.values(row).map((cell, ci) => (
                      <td key={ci}>{fmt(cell)}</td>
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
              {m.role === "agent" && (
                <span className={"dav-msg-tag " + (m.llm ? "dav-tag-llm" : "dav-tag-det")}>
                  {m.llm ? "LLM" : "deterministic"}
                </span>
              )}
              <div className="dav-msg-body">{m.text}</div>
            </div>
          ))}
          {asking && <div className="dav-msg dav-msg-agent"><div className="dav-msg-body">…thinking</div></div>}
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
