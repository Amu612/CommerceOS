import React from "react";
import "./IngestionControlBar.css";

export interface IngestionStatus {
  status: "stopped" | "running" | "paused";
  speed: number;
  simulated_date: string;
  events_processed: number;
  events_remaining: number;
  total_events: number;
  last_event_timestamp: string;
  orders_in_system: number;
}

interface IngestionControlBarProps {
  status: IngestionStatus;
  loading: boolean;
  onControl: (action: string, speed?: number, step?: number) => void;
}

export default function IngestionControlBar({
  status,
  loading,
  onControl,
}: IngestionControlBarProps) {
  const progressPct =
    status.total_events > 0
      ? Math.min(100, Math.round((status.events_processed / status.total_events) * 100))
      : 0;

  const formatTimestamp = (ts: string) => {
    if (!ts || ts === "N/A") return "N/A";
    try {
      return new Date(ts).toISOString().replace("T", " ").substring(0, 19);
    } catch {
      return ts;
    }
  };

  return (
    <div className="ingestion-control-bar">
      <div className="ingestion-bar-content">
        {/* Left: Identity & Status */}
        <div className="ingestion-title-section">
          <div className="ingestion-icon-badge">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
            </svg>
          </div>
          <div className="ingestion-title-text">
            <h3>
              Data Ingestion Engine
              <span className={`ingestion-status-pill ${status.status}`}>
                <span className={`status-dot ${status.status === "running" ? "pulsing" : ""}`} />
                {status.status}
              </span>
            </h3>
            <p>
              Simulated streaming replay from verified Nexus e-commerce datasets
            </p>
          </div>
        </div>

        {/* Center: Live Metrics */}
        <div className="ingestion-metrics-group">
          <div className="ingestion-metric-box">
            <span className="metric-name">Simulated Clock</span>
            <span className="metric-val">
              {formatTimestamp(status.simulated_date)}
              {status.events_remaining > 0 && (
                <span className="metric-behind" title="Every agent reads data only up to this simulated point in time — press Sync to Now to catch up to the full dataset.">
                  {" "}· {status.events_remaining.toLocaleString()} behind
                </span>
              )}
            </span>
          </div>

          <div className="ingestion-metric-box">
            <span className="metric-name">Ingested / Total</span>
            <span className="metric-val">
              {status.events_processed.toLocaleString()} / {status.total_events.toLocaleString()}
            </span>
          </div>

          <div className="ingestion-metric-box">
            <span className="metric-name">In System</span>
            <span className="metric-val">{status.orders_in_system.toLocaleString()} orders</span>
          </div>

          {/* Speed Pills */}
          <div className="ingestion-speed-controls" title="Adjust data ingestion speed">
            {[1, 10, 50, 200].map((s) => (
              <button
                key={s}
                type="button"
                className={`speed-pill-btn ${status.speed === s ? "active" : ""}`}
                onClick={() => onControl(status.status === "running" ? "start" : "resume", s)}
                disabled={loading}
              >
                {s}x
              </button>
            ))}
          </div>
        </div>

        {/* Right: Action Buttons */}
        <div className="ingestion-action-buttons">
          {status.status === "running" ? (
            <button
              type="button"
              className="btn-stream-action btn-pause"
              onClick={() => onControl("pause")}
              disabled={loading}
              title="Pause data flow"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                <rect x="6" y="4" width="4" height="16" />
                <rect x="14" y="4" width="4" height="16" />
              </svg>
              <span>Pause</span>
            </button>
          ) : (
            <button
              type="button"
              className="btn-stream-action btn-start"
              onClick={() => onControl(status.status === "paused" ? "resume" : "start")}
              disabled={loading}
              title="Start / Resume streaming"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                <polygon points="5 3 19 12 5 21 5 3" />
              </svg>
              <span>{status.status === "paused" ? "Resume" : "Start Stream"}</span>
            </button>
          )}

          <button
            type="button"
            className="btn-stream-action btn-step"
            onClick={() => onControl("step", undefined, 50)}
            disabled={loading || status.status === "running"}
            title="Step next 50 orders into the system"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
              <polygon points="5 4 15 12 5 20 5 4" />
              <line x1="19" y1="5" x2="19" y2="19" stroke="currentColor" strokeWidth="3" />
            </svg>
            <span>Step +50</span>
          </button>

          {status.events_remaining > 0 && (
            <button
              type="button"
              className="btn-stream-action btn-fastforward"
              onClick={() => onControl("complete")}
              disabled={loading || status.status === "running"}
              title="Ingest every remaining record right now — every agent reads the full dataset again"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                <polygon points="2 4 12 12 2 20 2 4" />
                <polygon points="12 4 22 12 12 20 12 4" />
              </svg>
              <span>Sync to Now</span>
            </button>
          )}

          <button
            type="button"
            className="btn-stream-action btn-reset"
            onClick={() => {
              if (window.confirm("Reset data ingestion and clear order records from system?")) {
                onControl("reset");
              }
            }}
            disabled={loading}
            title="Reset replay and clear active database"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="1 4 1 10 7 10" />
              <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
            </svg>
            <span>Reset</span>
          </button>
        </div>
      </div>

      {/* Real-time Progress Line */}
      <div
        className="ingestion-progress-line"
        style={{ width: `${progressPct}%` }}
        title={`Replay Progress: ${progressPct}%`}
      />
    </div>
  );
}
