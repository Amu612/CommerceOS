"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import "./DomainAgentView.css";
import { renderMarkdown } from "./lib/markdown";

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
  refreshKey,
  suggestions = [],
  headerExtra,
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
