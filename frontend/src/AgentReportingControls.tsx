"use client";

import React, { useState } from "react";
import "./AgentReportingControls.css";
import { AgentReportData, downloadHtmlFile, generateAgentReportHtml } from "./lib/reportGenerator";

export interface AgentReportingControlsProps {
  agentName: string;
  generateReportData: () => AgentReportData | Promise<AgentReportData>;
  disabled?: boolean;
}

export const AgentReportingControls: React.FC<AgentReportingControlsProps> = ({
  agentName,
  generateReportData,
  disabled = false,
}) => {
  const [reportingState, setReportingState] = useState<"idle" | "generating" | "ready">("idle");
  const [reportHtml, setReportHtml] = useState<string | null>(null);

  const handleStartReporting = async () => {
    setReportingState("generating");
    try {
      const reportData = await generateReportData();
      const html = generateAgentReportHtml(reportData);
      setReportHtml(html);
      setReportingState("ready");
    } catch (err) {
      console.error(`Failed to generate report for ${agentName}:`, err);
      setReportingState("idle");
      alert(`Could not generate report for ${agentName}. Please check agent connection.`);
    }
  };

  const handleDownloadReport = () => {
    if (!reportHtml) return;
    const sanitizedName = agentName.toLowerCase().replace(/[^a-z0-9]+/g, "_");
    const filename = `${sanitizedName}_report_${new Date().toISOString().slice(0, 10)}.html`;
    downloadHtmlFile(filename, reportHtml);
  };

  return (
    <div className="agent-reporting-container">
      <button
        type="button"
        className="report-btn report-btn-primary"
        onClick={handleStartReporting}
        disabled={disabled || reportingState === "generating"}
        title={`Generate an executive report for ${agentName}`}
      >
        {reportingState === "generating" ? (
          <>
            <span className="report-btn-spinner" />
            Generating Report…
          </>
        ) : reportingState === "ready" ? (
          <>↻ Regenerate Report</>
        ) : (
          <>📋 Start Reporting</>
        )}
      </button>

      <button
        type="button"
        className="report-btn report-btn-download"
        onClick={handleDownloadReport}
        disabled={reportingState !== "ready" || !reportHtml}
        title={reportingState === "ready" ? "Download the generated HTML report" : "Click Start Reporting first"}
      >
        ⬇️ Download Report
      </button>

      {reportingState === "ready" && (
        <span className="report-status-badge">✓ Report Ready</span>
      )}
    </div>
  );
};
