import { API_BASE_URL } from "../config";

const TOKEN_KEY = "commerceos_token";
const REFRESH_KEY = "commerceos_refresh";
const USER_KEY = "commerceos_user";

export type AuthUser = {
  id: string;
  username: string;
  email: string;
  role: string;
  /** Straight from the backend's `app.core.rbac` — the same source every API
   * route dependency reads from, so the nav this drives can never grant more
   * than the server itself would allow. */
  permitted_agents: string[];
  can_access_orchestrator: boolean;
  is_super_admin: boolean;
};

// Sessions are MEMORY-ONLY by design: the system must start with the login
// screen on every fresh app load (new tab, new deployment visit, restart) —
// nothing auth-related is ever persisted to localStorage/webStorage. Any
// legacy keys from older builds are wiped on first load below.
let memoryToken: string | null = null;
let _memoryRefresh: string | null = null;
let memoryUser: AuthUser | null = null;

try {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);
  localStorage.removeItem(USER_KEY);
  sessionStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(REFRESH_KEY);
  sessionStorage.removeItem(USER_KEY);
} catch {
  /* noop */
}

export function getToken(): string | null {
  return memoryToken;
}

export function getRefreshToken(): string | null {
  return _memoryRefresh;
}

export function getUser(): AuthUser | null {
  return memoryUser;
}

export function setSession(access: string, refresh: string, user: AuthUser) {
  memoryToken = access;
  _memoryRefresh = refresh;
  memoryUser = user;
}

export function clearSession() {
  memoryToken = null;
  _memoryRefresh = null;
  memoryUser = null;
}

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

/**
 * Global fetch interceptor: attaches the JWT to every same-API request and
 * routes 401s through the unauthorized handler. This lets the existing view
 * components (which call bare `fetch`) work unchanged once AUTH_ENFORCED=true.
 * Idempotent — safe to call more than once.
 */
export function installFetchAuth() {
  const w = window as unknown as { __commerceosFetchPatched?: boolean };
  if (w.__commerceosFetchPatched) return;
  w.__commerceosFetchPatched = true;

  const original = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url =
      typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const isApi = url.startsWith(API_BASE_URL) || url.startsWith("/api/");
    if (!isApi) return original(input, init);

    const token = getToken();
    const headers = new Headers(
      init.headers || (input instanceof Request ? input.headers : undefined),
    );
    if (token && !headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${token}`);
    }
    const res = await original(input, { ...init, headers });
    if (res.status === 401 && !url.includes("/auth/login")) {
      onUnauthorized?.();
    }
    return res;
  };
}

/** fetch wrapper: attaches the JWT, normalises errors, and signals 401s. */
export async function apiFetch<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const url = path.startsWith("http") ? path : `${API_BASE_URL}${path}`;
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);

  const res = await fetch(url, { ...init, headers });
  if (res.status === 401) {
    onUnauthorized?.();
    throw new ApiError("Session expired — please sign in again.", 401);
  }
  const text = await res.text();
  const body = text ? safeJson(text) : null;
  if (!res.ok) {
    const detail = (body && (body.detail || body.title || body.message)) || `Request failed (${res.status})`;
    throw new ApiError(detail, res.status, body);
  }
  return body as T;
}

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(message: string, status: number, body?: unknown) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

function safeJson(t: string): any {
  try {
    return JSON.parse(t);
  } catch {
    return { raw: t };
  }
}

export async function login(username: string, password: string): Promise<AuthUser> {
  const res = await fetch(`${API_BASE_URL}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(body?.detail || "Invalid username or password", res.status, body);
  setSession(body.access_token, body.refresh_token, body.user);
  return body.user as AuthUser;
}

/** Records the logout server-side (it lands in the audit log) before
 * discarding the local session — best-effort: an expired/already-invalid
 * token, or the network being down, must never block clearing the client
 * session, which is the part that actually matters for security. */
export async function logout(): Promise<void> {
  const token = getToken();
  if (token) {
    try {
      await fetch(`${API_BASE_URL}/api/v1/auth/logout`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
    } catch {
      /* offline / already expired — fall through and clear locally anyway */
    }
  }
  clearSession();
}
