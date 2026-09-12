import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { API_ENDPOINTS } from "../config";
import {
  AuthUser,
  getToken,
  getUser,
  login as apiLogin,
  logout as apiLogout,
  setUnauthorizedHandler,
} from "../lib/api";

type AuthState = {
  user: AuthUser | null;
  authRequired: boolean; // backend AUTH_ENFORCED
  ready: boolean;
  /** Set only when the session ended on its own (JWT expired) rather than a
   * user-initiated "Log out" click, so the login screen can say why. */
  sessionExpired: boolean;
  login: (u: string, p: string) => Promise<void>;
  logout: () => void;
};

const Ctx = createContext<AuthState>({
  user: null,
  authRequired: false,
  ready: false,
  sessionExpired: false,
  login: async () => {},
  logout: () => {},
});

/** Reads the `exp` claim (seconds since epoch) out of a JWT without
 * verifying it — verification is the server's job; this is only used to
 * proactively schedule a client-side logout at the moment a token was
 * always going to stop working, instead of waiting for the user's next API
 * call to bounce off a 401. */
function jwtExpiryMs(token: string): number | null {
  try {
    const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return typeof payload.exp === "number" ? payload.exp * 1000 : null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(getUser());
  const [authRequired, setAuthRequired] = useState(false);
  const [ready, setReady] = useState(false);
  const [sessionExpired, setSessionExpired] = useState(false);
  const expiryTimer = useRef<number | null>(null);

  const clearExpiryTimer = useCallback(() => {
    if (expiryTimer.current) {
      window.clearTimeout(expiryTimer.current);
      expiryTimer.current = null;
    }
  }, []);

  const logout = useCallback(
    (reason: "user" | "expired" = "user") => {
      clearExpiryTimer();
      setSessionExpired(reason === "expired");
      // Fire-and-forget: apiLogout() records the event server-side then
      // clears local storage: it never throws, so this is safe un-awaited
      // from a synchronous callback (e.g. the Log out button's onClick).
      void apiLogout();
      setUser(null);
    },
    [clearExpiryTimer],
  );

  // Reactive path: any API call that comes back 401 (token expired, revoked,
  // or the account deactivated server-side) logs the session out immediately
  // — the safety net for the proactive timer below missing edge cases (clock
  // drift, the tab having been asleep/backgrounded past the exp time).
  useEffect(() => {
    setUnauthorizedHandler(() => logout("expired"));
  }, [logout]);

  // Proactive path: schedule a logout for the exact moment the current
  // token's `exp` claim says it stops being valid, so a genuinely idle tab
  // (no API calls in flight to bounce off a 401) still ends the session on
  // time instead of silently keeping a dead-looking-active UI up.
  useEffect(() => {
    clearExpiryTimer();
    const token = getToken();
    if (!user || !token) return;
    const expMs = jwtExpiryMs(token);
    if (expMs === null) return;
    const delay = expMs - Date.now();
    if (delay <= 0) {
      logout("expired");
      return;
    }
    // setTimeout's delay argument is a 32-bit signed int internally; clamp
    // so a long-lived token (days) doesn't overflow into firing immediately.
    expiryTimer.current = window.setTimeout(() => logout("expired"), Math.min(delay, 2_147_000_000));
    return clearExpiryTimer;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user]);

  useEffect(() => {
    let cancelled = false;
    fetch(API_ENDPOINTS.system.status)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled) return;
        setAuthRequired(Boolean(d?.auth_enforced));
      })
      .catch(() => {})
      .finally(() => !cancelled && setReady(true));
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (u: string, p: string) => {
    const usr = await apiLogin(u, p);
    setSessionExpired(false);
    setUser(usr);
  }, []);

  const value = useMemo(
    () => ({ user, authRequired, ready, sessionExpired, login, logout: () => logout("user") }),
    [user, authRequired, ready, sessionExpired, login, logout],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export const useAuth = () => useContext(Ctx);
// eslint-disable-next-line react-refresh/only-export-components
export const isAuthed = () => Boolean(getToken());
