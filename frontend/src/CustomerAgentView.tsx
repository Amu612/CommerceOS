"use client";

import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import "./CustomerAgentView.css";
import { API_ENDPOINTS } from "./config";
import { renderMarkdown } from "./lib/markdown";
import { humanizeToolName } from "./lib/format";
import { AgentReportingControls } from "./AgentReportingControls";
import { AgentReportData, ChartSeries } from "./lib/reportGenerator";

type CustomerAgentViewProps = {
  agentsUrl?: string;
  queryUrl?: string;
  refreshKey?: number;
};

type ManifestAgent = {
  id: string;
  name: string;
  role: string;
  description: string;
};

type ToolCall = {
  tool?: string;
  name?: string;
  input?: unknown;
  output?: unknown;
};

type AgentTrace = {
  id: string;
  name: string;
  role: string;
  output: string;
  used_tools: string[];
};

type CustomerResponse = {
  response?: string;
  final_response?: string;
  category?: string;
  status?: string;
  agents_involved?: string[];
  traces?: AgentTrace[];
  tool_calls?: ToolCall[];
  retry_count?: number;
  supervisor_verdict?: string;
  order_context?: Record<string, unknown> | null;
  llm_backed?: boolean;
};

type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  streaming?: boolean;
  error?: boolean;
  meta?: CustomerResponse;
};

const PIPELINE = [
  { id: "triage", label: "Triage" },
  { id: "router", label: "Router" },
  { id: "context", label: "Context" },
  { id: "specialists", label: "Specialists" },
  { id: "supervisor", label: "Supervisor" },
];

const SUGGESTIONS = [
  "Where is my order #<id>?",
  "I want a refund for order <id>",
  "How much was I charged for order <id>?",
  "Do you sell any perfumaria products?",
];

function getAuthHeaders(): HeadersInit {
  if (typeof window === "undefined") return { "Content-Type": "application/json" };
  const token =
    localStorage.getItem("access_token") ||
    localStorage.getItem("token") ||
    sessionStorage.getItem("access_token") ||
    sessionStorage.getItem("token");
  return {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

export default function CustomerAgentView({
  agentsUrl = API_ENDPOINTS.customer.agents,
  queryUrl = API_ENDPOINTS.customer.query,
  refreshKey,
}: CustomerAgentViewProps) {
  const [agents, setAgents] = useState<ManifestAgent[]>([]);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [activeStage, setActiveStage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const streamTimer = useRef<number | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(agentsUrl)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then((data) => {
        if (!cancelled && Array.isArray(data?.agents)) setAgents(data.agents);
      })
      .catch(() => {
        /* backend warming up */
      });
    return () => {
      cancelled = true;
    };
  }, [agentsUrl, refreshKey]);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, activeStage]);

  useEffect(() => {
    return () => {
      if (streamTimer.current) window.clearInterval(streamTimer.current);
    };
  }, []);

  const streamInText = useCallback((id: string, fullText: string, meta: CustomerResponse) => {
    const words = fullText.split(/(\s+)/);
    let i = 0;
    if (streamTimer.current) window.clearInterval(streamTimer.current);
    streamTimer.current = window.setInterval(() => {
      i += 1;
      const partial = words.slice(0, i).join("");
      setMessages((prev) =>
        prev.map((m) =>
          m.id === id ? { ...m, text: partial, streaming: i < words.length } : m,
        ),
      );
      if (i >= words.length) {
        if (streamTimer.current) window.clearInterval(streamTimer.current);
        streamTimer.current = null;
        setMessages((prev) =>
          prev.map((m) => (m.id === id ? { ...m, text: fullText, streaming: false, meta } : m)),
        );
        setActiveStage(null);
        setLoading(false);
      }
    }, 28);
  }, []);

  const send = useCallback(
    async (raw: string) => {
      const text = raw.trim();
      if (!text || loading) return;

      const userId = `u-${Date.now()}`;
      const aiId = `a-${Date.now()}`;
      // Last few turns, sent so the agent can resolve follow-up questions
      // ("what about its status?") to an order mentioned earlier.
      const history = messages.slice(-6).map((m) => ({ role: m.role === "user" ? "user" : "assistant", text: m.text }));
      setMessages((prev) => [
        ...prev,
        { id: userId, role: "user", text },
        { id: aiId, role: "assistant", text: "", streaming: true },
      ]);
      setInput("");
      setLoading(true);
      setError(null);
      setActiveStage("triage");

      // Animate the pipeline stages while the request is in flight
      let stageIdx = 0;
      const stageTimer = window.setInterval(() => {
        stageIdx = Math.min(stageIdx + 1, PIPELINE.length - 1);
        setActiveStage(PIPELINE[stageIdx].id);
      }, 320);

      try {
        const res = await fetch(queryUrl, {
          method: "POST",
          headers: getAuthHeaders(),
          body: JSON.stringify({ query: text, history }),
        });
        window.clearInterval(stageTimer);
        if (!res.ok) throw new Error(`Customer agent returned ${res.status}`);
        const data = (await res.json()) as CustomerResponse;
        const answer =
          data.final_response || data.response || "No response generated.";
        setActiveStage("supervisor");
        streamInText(aiId, answer, data);
      } catch (err) {
        window.clearInterval(stageTimer);
        if (streamTimer.current) window.clearInterval(streamTimer.current);
        const msg =
          err instanceof Error ? err.message : "Customer agent request failed.";
        setError(msg);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === aiId
              ? { ...m, text: `⚠️ ${msg}`, streaming: false, error: true }
              : m,
          ),
        );
        setActiveStage(null);
        setLoading(false);
      }
    },
    [loading, queryUrl, streamInText, messages],
  );

  const lastUserMessage = useMemo(
    () => [...messages].reverse().find((m) => m.role === "user")?.text ?? "",
    [messages],
  );

  const buildReportData = useCallback((): AgentReportData => {
    const stageCounts: Record<string, number> = {};
    PIPELINE.forEach((s) => {
      stageCounts[s.label] = 1;
    });

    const pipelineSeries: ChartSeries[] = PIPELINE.map((s, idx) => ({
      label: s.label,
      value: (idx + 1) * 10,
      color: "#6366f1",
    }));

    const messageCount = messages.filter((m) => m.role === "user").length;
    const aiCount = messages.filter((m) => m.role === "assistant").length;

    const interactionSeries: ChartSeries[] = [
      { label: "Customer Questions", value: Math.max(1, messageCount), color: "#3b82f6" },
      { label: "Agent Responses", value: Math.max(1, aiCount), color: "#10b981" },
      { label: "Active Specialists", value: agents.length || 4, color: "#8b5cf6" },
    ];

    return {
      agentName: "Customer Support Agent",
      agentRole: "Multi-Agent Support Representative & Triage Pipeline",
      timestamp: new Date().toLocaleString(),
      health: "Healthy",
      confidencePct: 95,
      summary: `Multi-agent customer support pipeline with ${agents.length} active domain specialists. Handled ${messageCount} customer interactions with automated triage and context grounding.`,
      metrics: [
        { label: "Specialist Agents", value: agents.length },
        { label: "Questions Handled", value: messageCount },
        { label: "Agent Responses", value: aiCount },
        { label: "Active Stage", value: activeStage ? activeStage.toUpperCase() : "READY" },
      ],
      findings: [
        {
          title: "Inquiries routed through multi-specialist verification",
          severity: "LOW",
          category: "Triage & Routing",
          whatHappened: "Customer questions are routed across order tracking, returns, pricing, and fulfillment specialists before generating a verified answer.",
          whyItMatters: "Prevents hallucinations and ensures customers get accurate order information from the database.",
          recommendedAction: "Keep specialist tools updated with real-time order status feeds.",
        },
      ],
      recommendations: [
        {
          title: "Maintain instant order tracking cache",
          detail: "Cache high-frequency order lookup queries to provide sub-second responses to checking customers.",
          expectedImpact: "Lowers response latency and improves customer satisfaction score.",
          priority: "1",
        },
      ],
      charts: [
        { title: "SUPPORT WORKFLOW STAGES", type: "bar", data: pipelineSeries },
        { title: "INTERACTIONS & SPECIALIST COVERAGE", type: "donut", data: interactionSeries },
      ],
    };
  }, [messages, agents, activeStage]);

  return (
    <section className="cust-view-container">
      {/* Header */}
      <div className="cust-header">
        <div>
          <div className="cust-badge">
            <span className="cust-badge-dot" />
            <span>Customer Agent</span>
          </div>
          <h2 className="cust-header-title">Multi-Agent Support Representative</h2>
          <p className="cust-header-desc">
            Dynamic triage → router → specialist → supervisor workflow. Every answer is
            grounded on the same live Olist / DataCo transaction data streamed by the
            ingestion engine.
          </p>
          <div style={{ marginTop: "12px" }}>
            <AgentReportingControls
              agentName="Customer Agent"
              generateReportData={buildReportData}
              disabled={loading}
            />
          </div>
        </div>
        <div className="cust-pipeline">
          {PIPELINE.map((stage, idx) => (
            <React.Fragment key={stage.id}>
              <span
                className={
                  "cust-pipeline-node" +
                  (activeStage === stage.id ? " active" : "")
                }
              >
                {stage.label}
              </span>
              {idx < PIPELINE.length - 1 && (
                <span className="cust-pipeline-arrow">→</span>
              )}
            </React.Fragment>
          ))}
        </div>
      </div>

      {error && (
        <div className="cust-error">
          <strong>Customer Agent note:</strong> {error}
        </div>
      )}

      {/* Agent registry */}
      <div className="cust-section">
        <div className="cust-section-header">
          <h3 className="cust-section-title">Agent Registry</h3>
          <p className="cust-section-subtitle">Specialist agents the router can dispatch, loaded live.</p>
        </div>
        <div className="cust-agent-grid">
          {agents.length === 0 ? (
            <div className="cust-empty">Agent manifest unavailable — is the backend running?</div>
          ) : (
            agents.map((agent) => {
              const isActive =
                activeStage === agent.id ||
                (activeStage === "specialists" &&
                  !["triage", "router", "context", "supervisor"].includes(agent.id));
              return (
                <div
                  key={agent.id}
                  className={"cust-agent-card" + (isActive ? " active" : "")}
                >
                  <div className="cust-agent-card-head">
                    <span className="cust-agent-name">{agent.name}</span>
                    <span className="cust-agent-role">{agent.role}</span>
                  </div>
                  <p className="cust-agent-desc">{agent.description}</p>
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* Chat */}
      <div className="cust-section">
        <div className="cust-section-header">
          <h3 className="cust-section-title">Support Conversation</h3>
          <p className="cust-section-subtitle">
            Ask about orders, shipping, billing, refunds, or products. Include an Order ID
            for order-specific answers.
          </p>
        </div>

        <div className="cust-chat" ref={scrollRef}>
          {messages.length === 0 && (
            <div className="cust-chat-empty">
              <p>Start a conversation with the support representative.</p>
              <div className="cust-suggestions">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    type="button"
                    className="cust-suggestion"
                    onClick={() => setInput(s)}
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m) => (
            <div
              key={m.id}
              className={
                "cust-msg " + (m.role === "user" ? "cust-msg-user" : "cust-msg-ai")
              }
            >
              {m.role === "assistant" && m.meta?.category && (
                <div className="cust-msg-tags">
                  <span className="cust-tag">{m.meta.category}</span>
                  {(m.meta.agents_involved ?? [])
                    .filter(
                      (a) => !["triage", "router", "supervisor"].includes(a),
                    )
                    .map((a) => (
                      <span key={a} className="cust-tag cust-tag-agent">
                        {a}
                      </span>
                    ))}
                  {m.meta.retry_count ? (
                    <span className="cust-tag cust-tag-retry">
                      {m.meta.retry_count} retr{m.meta.retry_count > 1 ? "ies" : "y"}
                    </span>
                  ) : null}
                  <span
                    className={
                      "cust-tag " +
                      (m.meta.llm_backed ? "cust-tag-llm" : "cust-tag-det")
                    }
                  >
                    {m.meta.llm_backed ? "LLM synthesis" : "deterministic"}
                  </span>
                </div>
              )}

              <div className="cust-msg-body">
                {m.streaming ? m.text : renderMarkdown(m.text) ?? m.text}
                {m.streaming && <span className="cust-caret">▋</span>}
              </div>

              {m.role === "assistant" &&
                m.meta &&
                (m.meta.traces?.length || m.meta.tool_calls?.length) && (
                  <details className="cust-trace">
                    <summary>
                      Agent trace · {m.meta.traces?.length ?? 0} agent(s) ·{" "}
                      {m.meta.tool_calls?.length ?? 0} tool call(s)
                    </summary>
                    <div className="cust-trace-body">
                      {m.meta.order_context && (
                        <div className="cust-trace-block">
                          <span className="cust-trace-label">Order context</span>
                          <pre>{JSON.stringify(m.meta.order_context, null, 2)}</pre>
                        </div>
                      )}
                      {(m.meta.traces ?? []).map((t, i) => (
                        <div key={i} className="cust-trace-block">
                          <span className="cust-trace-label">
                            {t.name} ({t.role})
                            {t.used_tools?.length
                              ? ` · checked: ${t.used_tools.map(humanizeToolName).join(", ")}`
                              : ""}
                          </span>
                          <p>{renderMarkdown(t.output) ?? t.output}</p>
                        </div>
                      ))}
                      {(m.meta.tool_calls ?? []).map((c, i) => (
                        <div key={`tc-${i}`} className="cust-trace-block">
                          <span className="cust-trace-label">
                            🔧 {humanizeToolName(c.name ?? c.tool ?? `Step ${i + 1}`)}
                          </span>
                          {typeof c.output === "string" ? (
                            <p>{renderMarkdown(c.output) ?? c.output}</p>
                          ) : (
                            <pre>{JSON.stringify(c.output, null, 2)}</pre>
                          )}
                        </div>
                      ))}
                    </div>
                  </details>
                )}
            </div>
          ))}

          {activeStage && loading && (
            <div className="cust-msg cust-msg-ai">
              <div className="cust-thinking">
                <span className="cust-dot" />
                <span className="cust-dot" />
                <span className="cust-dot" />
                <span className="cust-thinking-label">
                  {PIPELINE.find((p) => p.id === activeStage)?.label ?? "Working"}…
                </span>
              </div>
            </div>
          )}
        </div>

        <div className="cust-input-group">
          <input
            className="cust-input"
            value={input}
            placeholder="Ask the support representative (e.g. 'Where is my order #...?')"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") send(input);
            }}
          />
          <button
            className="cust-btn-primary"
            disabled={loading || !input.trim()}
            onClick={() => send(input)}
          >
            {loading ? "Routing…" : "Send"}
          </button>
          {messages.length > 0 &&
            messages[messages.length - 1]?.error &&
            lastUserMessage && (
              <button
                className="cust-btn-secondary"
                disabled={loading}
                onClick={() => send(lastUserMessage)}
              >
                Retry
              </button>
            )}
        </div>
      </div>
    </section>
  );
}
