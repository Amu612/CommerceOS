import React, { useState } from "react";
import { useAuth } from "../auth/AuthContext";

export default function Login() {
  const { login } = useAuth();
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
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "radial-gradient(1200px 600px at 50% -10%, #10213a 0%, #080d14 60%)",
        fontFamily: "Inter, system-ui, sans-serif",
      }}
    >
      <form
        onSubmit={submit}
        style={{
          width: 380,
          background: "#0f172a",
          border: "1px solid #1e293b",
          borderRadius: 18,
          padding: 32,
          boxShadow: "0 24px 60px -20px rgba(0,0,0,0.6)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
          <div
            style={{
              width: 40,
              height: 40,
              borderRadius: 12,
              background: "linear-gradient(135deg,#3b82f6,#ec4899)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 20,
            }}
          >
            ⚡
          </div>
          <div>
            <div style={{ color: "#fff", fontWeight: 800, fontSize: 16 }}>CommerceOS / Nexus</div>
            <div style={{ color: "#64748b", fontSize: 11 }}>Multi-Agent Operations Platform</div>
          </div>
        </div>

        <label style={labelStyle}>Username</label>
        <input value={username} onChange={(e) => setUsername(e.target.value)} style={inputStyle} autoFocus />

        <label style={labelStyle}>Password</label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          style={inputStyle}
          placeholder="CommerceOS2024!"
        />

        {error && (
          <div style={{ color: "#fca5a5", fontSize: 12.5, marginTop: 10, marginBottom: 4 }}>{error}</div>
        )}

        <button
          type="submit"
          disabled={busy || !password}
          style={{
            marginTop: 16,
            width: "100%",
            padding: "11px 0",
            borderRadius: 11,
            border: "none",
            background: busy ? "#334155" : "#6366f1",
            color: "#fff",
            fontWeight: 700,
            fontSize: 14,
            cursor: busy ? "default" : "pointer",
          }}
        >
          {busy ? "Signing in…" : "Sign in"}
        </button>

        <p style={{ color: "#475569", fontSize: 11, marginTop: 16, lineHeight: 1.6 }}>
          Seeded roles: <code>admin</code>, <code>orders_admin</code>, <code>inventory_admin</code>,{" "}
          <code>support_admin</code>, <code>pricing_admin</code>, <code>marketing_admin</code>,{" "}
          <code>logistics_admin</code> — password <code>CommerceOS2024!</code>
        </p>
      </form>
    </div>
  );
}

const labelStyle: React.CSSProperties = {
  display: "block",
  fontSize: 11,
  fontWeight: 700,
  letterSpacing: "0.06em",
  textTransform: "uppercase",
  color: "#64748b",
  marginTop: 12,
  marginBottom: 5,
};
const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "10px 12px",
  borderRadius: 10,
  border: "1px solid #334155",
  background: "#0a0f1a",
  color: "#fff",
  fontSize: 14,
  outline: "none",
};
