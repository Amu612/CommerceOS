"use client";

import React, { useCallback, useEffect, useState } from "react";
import "./ApprovalsView.css";
import { apiFetch } from "./lib/api";

type Approval = {
  id: string;
  agent: string;
  action_type: string;
  title: string;
  detail?: string;
  mode: string;
  status: string;
  confidence?: number;
  payload?: Record<string, unknown>;
  result?: Record<string, unknown> | null;
  verification?: { verified?: boolean } | null;
  created_at?: string;
  executed_at?: string;
  approval?: { id: string; required_role: string; status: string; decided_by?: string; reason?: string };
};

const SEV_COLOR: Record<string, string> = { CRITICAL: "#b91c1c", HIGH: "#c2410c", MEDIUM: "#b45309", LOW: "#0369a1" };
const STATUS_COLOR: Record<string, string> = {
  VERIFIED: "#0f766e",
  EXECUTED: "#0f766e",
  PROPOSED: "#b45309",
  BLOCKED: "#b91c1c",
  REJECTED: "#857f93",
  FAILED: "#b91c1c",
};

export default function ApprovalsView({ refreshKey }: { refreshKey?: number }) {
  const [pending, setPending] = useState<Approval[]>([]);
  const [actions, setActions] = useState<Approval[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [ap, act] = await Promise.all([
        apiFetch<{ items: Approval[] }>("/api/v1/automation/approvals?status=PENDING"),
        apiFetch<{ items: Approval[]; counts: Record<string, number> }>("/api/v1/automation/actions?limit=60"),
      ]);
      setPending(ap.items);
      setActions(act.items);
      setCounts(act.counts);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load automation queue");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  const decide = async (approvalId: string, approve: boolean) => {
    setBusyId(approvalId);
    try {
      const reason = approve ? "Approved via console" : window.prompt("Reason for rejection (optional):") ?? "";
      await apiFetch(`/api/v1/automation/approvals/${approvalId}/${approve ? "approve" : "reject"}`, {
        method: "POST",
        body: JSON.stringify({ reason }),
      });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Decision failed");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="apv">
      <div className="apv-header">
        <div>
          <div className="apv-badge">
            <span className="apv-dot" /> HUMAN-IN-THE-LOOP
          </div>
          <h2 className="apv-title">Automation & Approvals</h2>
          <p className="apv-sub">
            Agents propose actions from their findings. Low-blast-radius actions (internal flags,
            promised-date buffers, queue hints) auto-execute and self-verify. Everything that moves
            money or touches customers waits for the owning domain admin.
          </p>
        </div>
        <button className="apv-btn" onClick={load} disabled={loading}>
          {loading ? "Loading…" : "Refresh"}
        </button>
      </div>

      {error && (
        <div className="apv-error">
          <strong>Automation note:</strong> {error}
        </div>
      )}

      <div className="apv-kpis">
        <Kpi label="Auto-executed" value={counts.auto_executed ?? 0} tone="ok" />
        <Kpi label="Pending Approval" value={pending.length} tone="warn" />
        <Kpi label="Blocked (policy)" value={counts.blocked ?? 0} tone="crit" />
      </div>

      <div className="apv-section">
        <h3 className="apv-section-title">Pending Your Approval</h3>
        {pending.length === 0 ? (
          <div className="apv-empty">Nothing waiting — run the Orchestrator or an agent to generate proposals.</div>
        ) : (
          <div className="apv-list">
            {pending.map((a) => (
              <div key={a.id} className="apv-card">
                <div className="apv-card-head">
                  <span className="apv-sev" style={{ background: SEV_COLOR[String(a.payload?.severity ?? "MEDIUM")] ?? "#475569" }}>
                    {String(a.payload?.severity ?? "MEDIUM")}
                  </span>
                  <span className="apv-card-title">{a.title}</span>
                  <span className="apv-tag">{a.agent}</span>
                  <span className="apv-tag">{a.action_type}</span>
                  <span className="apv-tag apv-tag-role">→ {a.approval?.required_role}</span>
                </div>
                {a.detail && <p className="apv-card-detail">{a.detail}</p>}
                {a.payload?.evidence ? <p className="apv-card-evidence">Evidence: {String(a.payload.evidence)}</p> : null}
                <div className="apv-card-actions">
                  <button
                    className="apv-approve"
                    disabled={busyId === a.approval?.id}
                    onClick={() => a.approval && decide(a.approval.id, true)}
                  >
                    ✓ Approve & execute
                  </button>
                  <button
                    className="apv-reject"
                    disabled={busyId === a.approval?.id}
                    onClick={() => a.approval && decide(a.approval.id, false)}
                  >
                    ✕ Reject
                  </button>
                  <span className="apv-conf">confidence {Math.round((a.confidence ?? 0) * 100)}%</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="apv-section">
        <h3 className="apv-section-title">Recent Actions</h3>
        <div className="apv-table-wrap">
          <table className="apv-table">
            <thead>
              <tr>
                <th>Status</th>
                <th>Mode</th>
                <th>Agent</th>
                <th>Action</th>
                <th>Title</th>
                <th>Verified</th>
                <th>Recorded effect</th>
              </tr>
            </thead>
            <tbody>
              {actions.map((a) => (
                <tr key={a.id}>
                  <td>
                    <span style={{ color: STATUS_COLOR[a.status] ?? "#cbd5e1", fontWeight: 700 }}>{a.status}</span>
                  </td>
                  <td>{a.mode}</td>
                  <td>{a.agent}</td>
                  <td className="apv-mono">{a.action_type}</td>
                  <td>{a.title}</td>
                  <td>{a.verification?.verified ? "✓" : a.status === "BLOCKED" ? "—" : ""}</td>
                  <td className="apv-effect">{describeEffect(a.result)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

function describeEffect(result?: Record<string, unknown> | null): string {
  if (!result) return "—";
  const effect = result.effect;
  if (typeof effect === "string") {
    const label = effect.replace(/_/g, " ").toLowerCase();
    const bits = ["carrier", "scope", "segment", "product_id", "floor_pct", "notification_id"]
      .map((k) => (result[k] != null && result[k] !== "" ? `${k.replace(/_/g, " ")}: ${result[k]}` : null))
      .filter(Boolean);
    return bits.length ? `${label} (${bits.join(", ")})` : label;
  }
  if (result.notification_id) return "Notification recorded";
  if (result.error) return `Failed: ${String(result.error)}`;
  return "—";
}

function Kpi({ label, value, tone }: { label: string; value: number; tone: "ok" | "warn" | "crit" }) {
  return (
    <div className={`apv-kpi apv-kpi-${tone}`}>
      <span className="apv-kpi-value">{value}</span>
      <span className="apv-kpi-label">{label}</span>
    </div>
  );
}
