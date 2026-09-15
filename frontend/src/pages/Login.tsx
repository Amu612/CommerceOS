import React, { useState } from "react";
import { useAuth } from "../auth/AuthContext";
import "./Login.css";

const SEEDED_ROLES = [
  "admin",
  "orders_admin",
  "inventory_admin",
  "support_admin",
  "pricing_admin",
  "marketing_admin",
  "logistics_admin",
];

export default function Login() {
  const { login, sessionExpired } = useAuth();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Sign-in failed";
      if (msg.includes("Failed to fetch") || msg.includes("NetworkError") || msg.includes("Load failed")) {
        setError("Network error: Unable to reach backend server. Please verify the backend container is running and reachable.");
      } else {
        setError(msg);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-page">
      <form className="login-card" onSubmit={submit}>
        <div className="login-crest">
          <svg viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M24 3 L41 12 V26 C41 36 33.5 42.5 24 45 C14.5 42.5 7 36 7 26 V12 Z"
              fill="var(--accent-primary)" stroke="var(--accent-gold)" strokeWidth="1.2" />
            <path d="M24 10 L34 15.5 V26 C34 32.5 29.7 37 24 39 C18.3 37 14 32.5 14 26 V15.5 Z"
              fill="none" stroke="var(--accent-gold-light)" strokeWidth="1" opacity="0.6" />
            <path d="M17 24.5 L22 29.5 L31 19" stroke="var(--accent-gold-light)" strokeWidth="2.4"
              strokeLinecap="round" strokeLinejoin="round" fill="none" />
          </svg>
          <div className="login-brand">CommerceOS</div>
          <div className="login-rule" />
          <div className="login-tagline">Autonomous Operations Console</div>
        </div>

        <div className="login-field">
          <label className="login-label" htmlFor="login-username">Username</label>
          <input
            id="login-username"
            className="login-input"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus
            autoComplete="username"
          />
        </div>

        <div className="login-field">
          <label className="login-label" htmlFor="login-password">Password</label>
          <input
            id="login-password"
            className="login-input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••••••"
            autoComplete="current-password"
          />
        </div>

        {!error && sessionExpired && (
          <div className="login-error">Your session expired — please sign in again.</div>
        )}
        {error && <div className="login-error">{error}</div>}

        <button type="submit" className="login-submit" disabled={busy || !password}>
          {busy ? "Signing in…" : "Sign In"}
        </button>

        <div className="login-roles">
          <p className="login-roles-label">
            Seeded roles &bull; Default password: <code style={{ color: "var(--accent-gold)", fontWeight: 600, userSelect: "all" }}>CommerceOS2026!</code>
          </p>
          <div className="login-role-chips">
            {SEEDED_ROLES.map((role) => (
              <button
                type="button"
                key={role}
                className={`login-role-chip${username === role ? " active" : ""}`}
                onClick={() => {
                  setUsername(role);
                  if (!password) setPassword("CommerceOS2026!");
                }}
              >
                {role}
              </button>
            ))}
          </div>
        </div>
      </form>
    </div>
  );
}
