import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import {
  authLogout,
  authMe,
  getToken,
  setToken,
  setUnauthorizedHandler,
  type AuthUser,
} from "../api/client";

interface AuthCtx {
  user: AuthUser | null;
  loading: boolean;
  isAdmin: boolean;
  isViewer: boolean;
  canWrite: boolean;        // false for the read-only "viewer" role
  // "vault_matcher" — restricted role, full read/write but ONLY on the
  // Employee Matcher and File Vault pages (enforced server-side too, see
  // backend api/deps.require_full_access). Nav/routing hide everything else.
  isVaultMatcherOnly: boolean;
  setSession: (token: string, user: AuthUser) => void;
  logout: () => void;
}

const Ctx = createContext<AuthCtx>({
  user: null,
  loading: true,
  isAdmin: false,
  isViewer: false,
  canWrite: false,
  isVaultMatcherOnly: false,
  setSession: () => {},
  logout: () => {},
});

export const useAuth = () => useContext(Ctx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    (async () => {
      if (getToken()) {
        try {
          setUser(await authMe());
        } catch {
          setToken(null);
        }
      }
      setLoading(false);
    })();
  }, []);

  const setSession = (token: string, u: AuthUser) => {
    setToken(token);
    setUser(u);
  };
  const logout = () => {
    // Best-effort server-side revocation (denylist the token), then clear locally.
    authLogout().catch(() => {});
    setToken(null);
    setUser(null);
  };

  const role = user?.role;
  return (
    <Ctx.Provider
      value={{
        user,
        loading,
        isAdmin: role === "admin",
        isViewer: role === "viewer",
        canWrite: role === "admin" || role === "user" || role === "vault_matcher",
        isVaultMatcherOnly: role === "vault_matcher",
        setSession,
        logout,
      }}
    >
      {children}
    </Ctx.Provider>
  );
}
