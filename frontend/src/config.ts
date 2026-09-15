function getApiBaseUrl(): string {
  const envUrl = import.meta.env.VITE_API_BASE_URL;
  if (envUrl && envUrl !== "http://localhost:8000" && envUrl !== "http://placeholder") {
    return envUrl.replace(/\/+$/, "");
  }
  if (typeof window !== "undefined") {
    // When running in a deployed browser (e.g. EC2 IP, custom domain, ALB)
    if (window.location.hostname !== "localhost" && window.location.hostname !== "127.0.0.1") {
      return ""; // relative same-origin /api/...
    }
    // When served by Nginx container locally
    if (window.location.port === "80" || window.location.port === "8080") {
      return "";
    }
  }
  return "http://localhost:8000";
}

function getWsBaseUrl(apiBase: string): string {
  const envWs = import.meta.env.VITE_WS_BASE_URL;
  if (envWs && envWs !== "ws://localhost:8000" && envWs !== "ws://placeholder") {
    return envWs.replace(/\/+$/, "");
  }
  if (apiBase.startsWith("http")) {
    return apiBase.replace(/^http/, "ws");
  }
  if (typeof window !== "undefined") {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${window.location.host}`;
  }
  return "ws://localhost:8000";
}

const API_BASE_URL = getApiBaseUrl();
const WS_BASE_URL = getWsBaseUrl(API_BASE_URL);

export const API_ENDPOINTS = {
  orders: {
    analyze: `${API_BASE_URL}/api/orders/analyze`,
    query: `${API_BASE_URL}/api/orders/query`,
    latest: `${API_BASE_URL}/api/orders/latest`,
    health: `${API_BASE_URL}/api/orders/health`,
    reset: `${API_BASE_URL}/api/orders/reset`,
    ingestion: {
      status: `${API_BASE_URL}/api/orders/ingestion/status`,
      control: `${API_BASE_URL}/api/orders/ingestion/control`,
    },
  },
  inventory: {
    monitor: `${API_BASE_URL}/api/v1/agents/inventory/monitor`,
    query: `${API_BASE_URL}/api/v1/agents/inventory/query`,
    reorder: `${API_BASE_URL}/api/v1/agents/inventory/reorder`,
  },
  customer: {
    agents: `${API_BASE_URL}/api/customer/agents`,
    query: `${API_BASE_URL}/api/customer/query`,
    stream: `${API_BASE_URL}/api/customer/stream`,
  },
  logistics: {
    analyze: `${API_BASE_URL}/api/v1/agents/logistics/analyze`,
    query: `${API_BASE_URL}/api/v1/agents/logistics/query`,
    route: `${API_BASE_URL}/api/v1/agents/logistics/route`,
  },
  pricing: {
    analyze: `${API_BASE_URL}/api/v1/agents/pricing/analyze`,
    query: `${API_BASE_URL}/api/v1/agents/pricing/query`,
  },
  marketing: {
    analyze: `${API_BASE_URL}/api/v1/agents/marketing/analyze`,
    query: `${API_BASE_URL}/api/v1/agents/marketing/query`,
  },
  orchestrator: {
    run: `${API_BASE_URL}/api/v1/orchestrator/run`,
    latest: `${API_BASE_URL}/api/v1/orchestrator/latest`,
  },
  system: {
    llm: `${API_BASE_URL}/api/v1/system/llm`,
    status: `${API_BASE_URL}/api/v1/system/status`,
  },
  simulation: {
    status: `${API_BASE_URL}/api/simulation/status`,
    control: `${API_BASE_URL}/api/simulation/control`,
  },
  pricingCompetitorFeed: {
    status: `${API_BASE_URL}/api/v1/agents/pricing/competitor-feed`,
    sync: `${API_BASE_URL}/api/v1/agents/pricing/competitor-feed/sync`,
  },
} as const;

export { API_BASE_URL, WS_BASE_URL };