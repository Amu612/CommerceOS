import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { API_ENDPOINTS } from "../config";
import {
  AuthUser,
  clearSession,
  getToken,
  getUser,
  login as apiLogin,
  setUnauthorizedHandler,
} from "../lib/api";

type AuthState = {
  user: AuthUser | null;
  authRequired: boolean; // backend AUTH_ENFORCED
  ready: boolean;
  login: (u: string, p: string) => Promise<void>;
  logout: () => void;
};

const Ctx = createContext<AuthState>({
  user: null,
  authRequired: false,
  ready: false,
  login: async () => {},
  logout: () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(getUser());
  const [authRequired, setAuthRequired] = useState(false);
  const [ready, setReady] = useState(false);

  const logout = useCallback(() => {
    clearSession();
    setUser(null);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(logout);
  }, [logout]);

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
    setUser(usr);
  }, []);

  const value = useMemo(
    () => ({ user, authRequired, ready, login, logout }),
    [user, authRequired, ready, login, logout],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export const useAuth = () => useContext(Ctx);
// eslint-disable-next-line react-refresh/only-export-components
export const isAuthed = () => Boolean(getToken());
