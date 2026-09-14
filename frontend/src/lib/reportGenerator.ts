/**
 * Agent Report Generator
 * Generates an agent-specific, self-contained HTML report complete with:
 * - Agent details, timestamp, and status
 * - Executive Summary written in clear, simple vocabulary
 * - Key Metrics cards
 * - Embedded SVG Charts (bar charts, donut charts)
 * - Findings (severity, what happened, why it matters, simple recommended action)
 * - Recommendations list
 * - Printable / PDF-friendly formatting
 */

export interface ReportMetric {
  label: string;
  value: string | number;
  unit?: string;
  description?: string;
}

export interface ReportFinding {
  title: string;
  severity: string;
  category?: string;
  whatHappened: string;
  whyItMatters: string;
  recommendedAction: string;
  evidence?: string;
}

export interface ReportRecommendation {
  title: string;
  detail: string;
  expectedImpact?: string;
  priority?: string;
}

export interface ChartSeries {
  label: string;
  value: number;
  color?: string;
}

export interface AgentReportData {
  agentName: string;
  agentRole: string;
  timestamp: string;
  health?: string;
  confidencePct?: number;
  summary: string;
  metrics: ReportMetric[];
  findings: ReportFinding[];
  recommendations: ReportRecommendation[];
  charts?: {
    title: string;
    description?: string;
    type: "bar" | "donut";
    data: ChartSeries[];
  }[];
}

function escapeHtml(str: string): string {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function renderSvgBarChart(title: string, data: ChartSeries[]): string {
  if (!data || data.length === 0) return "";
  const maxVal = Math.max(...data.map((d) => d.value), 1);
  const chartHeight = 220;
  const barWidth = 44;
  const gap = 24;
  const chartWidth = Math.max(data.length * (barWidth + gap) + 60, 360);
  const plotHeight = 150;
  const topPad = 25;

  const bars = data
    .map((item, i) => {
      const barH = Math.max(Math.round((item.value / maxVal) * plotHeight), 4);
      const x = 40 + i * (barWidth + gap);
      const y = topPad + plotHeight - barH;
      const color = item.color || "#6366f1";
      const shortLabel = item.label.length > 12 ? item.label.slice(0, 11) + "…" : item.label;

      return `
        <g class="bar-group">
          <rect x="${x}" y="${y}" width="${barWidth}" height="${barH}" rx="4" fill="${color}" opacity="0.9" />
          <text x="${x + barWidth / 2}" y="${y - 6}" font-size="11" font-weight="600" fill="#334155" text-anchor="middle">
            ${item.value.toLocaleString()}
          </text>
          <text x="${x + barWidth / 2}" y="${topPad + plotHeight + 18}" font-size="11" fill="#64748b" text-anchor="middle">
            ${escapeHtml(shortLabel)}
          </text>
        </g>
      `;
    })
    .join("");

  return `
    <div class="report-chart-box">
      <div class="report-chart-title">${escapeHtml(title)}</div>
      <svg width="100%" height="${chartHeight}" viewBox="0 0 ${chartWidth} ${chartHeight}" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg">
        <line x1="20" y1="${topPad + plotHeight}" x2="${chartWidth - 20}" y2="${topPad + plotHeight}" stroke="#cbd5e1" stroke-width="1.5" />
        ${bars}
      </svg>
    </div>
  `;
}

function renderSvgDonutChart(title: string, data: ChartSeries[]): string {
  if (!data || data.length === 0) return "";
  const total = Math.max(data.reduce((acc, d) => acc + d.value, 0), 1);
  const colors = ["#6366f1", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4"];
  const radius = 55;
  const strokeWidth = 24;
  const circumference = 2 * Math.PI * radius;

  let currentAngle = 0;
  const slices = data
    .map((item, idx) => {
      const share = item.value / total;
      const strokeDash = `${share * circumference} ${circumference}`;
      const strokeOffset = -currentAngle * circumference;
      currentAngle += share;
      const color = item.color || colors[idx % colors.length];

      return `
        <circle cx="90" cy="90" r="${radius}" fill="transparent"
          stroke="${color}" stroke-width="${strokeWidth}"
          stroke-dasharray="${strokeDash}" stroke-dashoffset="${strokeOffset}" />
      `;
    })
    .join("");

  const legend = data
    .map((item, idx) => {
      const color = item.color || colors[idx % colors.length];
      const pct = Math.round((item.value / total) * 100);
      return `
        <div class="donut-legend-item">
          <span class="donut-color-dot" style="background:${color};"></span>
          <span class="donut-label">${escapeHtml(item.label)}:</span>
          <strong>${item.value.toLocaleString()} (${pct}%)</strong>
        </div>
      `;
    })
    .join("");

  return `
    <div class="report-chart-box">
      <div class="report-chart-title">${escapeHtml(title)}</div>
      <div class="donut-container">
        <svg width="180" height="180" viewBox="0 0 180 180" xmlns="http://www.w3.org/2000/svg" style="transform: rotate(-90deg);">
          ${slices}
        </svg>
        <div class="donut-legend">
          ${legend}
        </div>
      </div>
    </div>
  `;
}

export function generateAgentReportHtml(report: AgentReportData): string {
  const chartHtml = (report.charts ?? [])
    .map((c) => (c.type === "donut" ? renderSvgDonutChart(c.title, c.data) : renderSvgBarChart(c.title, c.data)))
    .join("");

  const metricsHtml = (report.metrics ?? [])
    .map(
      (m) => `
      <div class="metric-card">
        <div class="metric-label">${escapeHtml(m.label)}</div>
        <div class="metric-value">${escapeHtml(String(m.value))} ${m.unit ? `<span class="metric-unit">${escapeHtml(m.unit)}</span>` : ""}</div>
        ${m.description ? `<div class="metric-desc">${escapeHtml(m.description)}</div>` : ""}
      </div>
    `,
    )
    .join("");

  const findingsHtml =
    report.findings.length === 0
      ? `<div class="empty-state">No issues found. Everything is operating normally.</div>`
      : report.findings
          .map((f) => {
            const sevClass = (f.severity || "info").toLowerCase();
            return `
        <div class="finding-item sev-${sevClass}">
          <div class="finding-header">
            <span class="badge badge-${sevClass}">${escapeHtml(f.severity || "INFO")}</span>
            <span class="finding-title">${escapeHtml(f.title)}</span>
          </div>
          <div class="finding-body">
            <p><strong>What happened:</strong> ${escapeHtml(f.whatHappened)}</p>
            <p><strong>Why it matters:</strong> ${escapeHtml(f.whyItMatters)}</p>
            <p><strong>Recommended Action:</strong> ${escapeHtml(f.recommendedAction)}</p>
          </div>
        </div>
      `;
          })
          .join("");

  const recsHtml =
    report.recommendations.length === 0
      ? `<div class="empty-state">No specific recommendations at this moment.</div>`
      : report.recommendations
          .map(
            (r, i) => `
        <div class="rec-card">
          <div class="rec-num">#${i + 1}</div>
          <div class="rec-content">
            <div class="rec-title">${escapeHtml(r.title)}</div>
            <div class="rec-detail">${escapeHtml(r.detail)}</div>
            ${r.expectedImpact ? `<div class="rec-impact"><strong>Expected Benefit:</strong> ${escapeHtml(r.expectedImpact)}</div>` : ""}
          </div>
        </div>
      `,
          )
          .join("");

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>${escapeHtml(report.agentName)} - Agent Report</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      color: #1e293b;
      background: #f8fafc;
      line-height: 1.5;
      padding: 32px 20px;
    }
    .report-wrapper {
      max-width: 960px;
      margin: 0 auto;
      background: #ffffff;
      border: 1px solid #e2e8f0;
      border-radius: 12px;
      padding: 40px;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.04);
    }
    .report-header {
      border-bottom: 2px solid #f1f5f9;
      padding-bottom: 24px;
      margin-bottom: 28px;
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      flex-wrap: wrap;
      gap: 16px;
    }
    .report-title-area h1 {
      font-size: 26px;
      font-weight: 700;
      color: #0f172a;
    }
    .report-tagline {
      font-size: 14px;
      color: #64748b;
      margin-top: 4px;
    }
    .report-meta {
      text-align: right;
      font-size: 13px;
      color: #64748b;
    }
    .meta-badge {
      display: inline-block;
      padding: 4px 10px;
      border-radius: 20px;
      font-weight: 600;
      font-size: 12px;
      margin-bottom: 6px;
      background: #e0e7ff;
      color: #4338ca;
    }
    .section-title {
      font-size: 18px;
      font-weight: 700;
      color: #0f172a;
      margin: 28px 0 16px 0;
      padding-bottom: 8px;
      border-bottom: 1px solid #e2e8f0;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .summary-box {
      background: #f8fafc;
      border-left: 4px solid #6366f1;
      padding: 16px 20px;
      border-radius: 6px;
      font-size: 15px;
      color: #334155;
      margin-bottom: 24px;
      line-height: 1.6;
    }
    .metrics-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .metric-card {
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      padding: 16px;
    }
    .metric-label {
      font-size: 12px;
      font-weight: 600;
      color: #64748b;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .metric-value {
      font-size: 22px;
      font-weight: 700;
      color: #0f172a;
      margin: 6px 0 2px 0;
    }
    .metric-unit {
      font-size: 14px;
      font-weight: 500;
      color: #64748b;
    }
    .metric-desc {
      font-size: 11px;
      color: #94a3b8;
    }
    .charts-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
      gap: 20px;
      margin-bottom: 28px;
    }
    .report-chart-box {
      background: #ffffff;
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      padding: 18px;
    }
    .report-chart-title {
      font-size: 14px;
      font-weight: 600;
      color: #334155;
      margin-bottom: 12px;
    }
    .donut-container {
      display: flex;
      align-items: center;
      gap: 20px;
      flex-wrap: wrap;
    }
    .donut-legend {
      display: flex;
      flex-direction: column;
      gap: 6px;
      font-size: 12px;
    }
    .donut-legend-item {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .donut-color-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      display: inline-block;
    }
    .finding-item {
      border: 1px solid #e2e8f0;
      border-left-width: 4px;
      border-radius: 8px;
      padding: 16px 20px;
      margin-bottom: 14px;
      background: #ffffff;
    }
    .sev-critical, .sev-high { border-left-color: #ef4444; }
    .sev-medium, .sev-warning { border-left-color: #f59e0b; }
    .sev-low, .sev-info { border-left-color: #3b82f6; }
    .badge {
      font-size: 11px;
      font-weight: 700;
      padding: 3px 8px;
      border-radius: 4px;
      text-transform: uppercase;
      display: inline-block;
      margin-right: 8px;
    }
    .badge-critical, .badge-high { background: #fee2e2; color: #991b1b; }
    .badge-medium, .badge-warning { background: #fef3c7; color: #92400e; }
    .badge-low, .badge-info { background: #e0f2fe; color: #075985; }
    .finding-title {
      font-size: 15px;
      font-weight: 700;
      color: #0f172a;
    }
    .finding-body {
      margin-top: 10px;
      font-size: 13.5px;
      color: #334155;
    }
    .finding-body p { margin-bottom: 6px; }
    .rec-card {
      display: flex;
      gap: 16px;
      padding: 16px;
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      margin-bottom: 12px;
    }
    .rec-num {
      font-size: 16px;
      font-weight: 700;
      color: #6366f1;
      width: 32px;
      height: 32px;
      background: #e0e7ff;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }
    .rec-title {
      font-size: 14.5px;
      font-weight: 600;
      color: #0f172a;
      margin-bottom: 4px;
    }
    .rec-detail {
      font-size: 13px;
      color: #475569;
    }
    .rec-impact {
      font-size: 12px;
      color: #10b981;
      margin-top: 6px;
    }
    .empty-state {
      padding: 20px;
      text-align: center;
      color: #64748b;
      font-size: 14px;
      background: #f8fafc;
      border-radius: 8px;
      border: 1px dashed #cbd5e1;
    }
    .footer {
      margin-top: 40px;
      padding-top: 16px;
      border-top: 1px solid #e2e8f0;
      font-size: 12px;
      color: #94a3b8;
      display: flex;
      justify-content: space-between;
    }
    @media print {
      body { background: #ffffff; padding: 0; }
      .report-wrapper { border: none; box-shadow: none; padding: 0; }
    }
  </style>
</head>
<body>
  <div class="report-wrapper">
    <header class="report-header">
      <div class="report-title-area">
        <span class="meta-badge">${escapeHtml(report.agentName.toUpperCase())} REPORT</span>
        <h1>${escapeHtml(report.agentName)}</h1>
        <div class="report-tagline">${escapeHtml(report.agentRole)}</div>
      </div>
      <div class="report-meta">
        <div><strong>Generated:</strong> ${escapeHtml(report.timestamp)}</div>
        ${report.health ? `<div><strong>Status:</strong> ${escapeHtml(report.health)}</div>` : ""}
        ${report.confidencePct !== undefined ? `<div><strong>Confidence:</strong> ${report.confidencePct}%</div>` : ""}
      </div>
    </header>

    <div class="section-title">Executive Summary</div>
    <div class="summary-box">
      ${escapeHtml(report.summary || "All operational metrics have been calculated for this agent.")}
    </div>

    ${report.metrics.length > 0 ? `
      <div class="section-title">Key Numbers &amp; Metrics</div>
      <div class="metrics-grid">
        ${metricsHtml}
      </div>
    ` : ""}

    ${chartHtml ? `
      <div class="section-title">Visual Charts &amp; Trends</div>
      <div class="charts-grid">
        ${chartHtml}
      </div>
    ` : ""}

    <div class="section-title">Findings (Issues Detected)</div>
    <div class="findings-list">
      ${findingsHtml}
    </div>

    <div class="section-title">Recommended Next Steps</div>
    <div class="recs-list">
      ${recsHtml}
    </div>

    <footer class="footer">
      <span>Agentic E-Commerce Platform</span>
      <span>Report generated on demand</span>
    </footer>
  </div>
</body>
</html>`;
}

export function downloadHtmlFile(filename: string, content: string) {
  const blob = new Blob([content], { type: "text/html;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.setAttribute("download", filename);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
