import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import {
  getPortalToken,
  portalLogout,
  portalMe,
  setPortalToken,
  setPortalUnauthorizedHandler,
  type PortalUser,
} from "../api/client";

interface PortalAuthCtx {
  user: PortalUser | null;
  loading: boolean;
  setSession: (token: string, user: PortalUser) => void;
  logout: () => void;
}

const Ctx = createContext<PortalAuthCtx>({
  user: null,
  loading: true,
  setSession: () => {},
  logout: () => {},
});

export const usePortalAuth = () => useContext(Ctx);

/** Entirely separate from lib/auth.tsx's AuthProvider — own token, own
 *  /portal/auth/me check, own logout. Scoped to the /portal/* route subtree
 *  in App.tsx so it never runs for internal-app usage. */
export function PortalAuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<PortalUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setPortalUnauthorizedHandler(() => setUser(null));
    (async () => {
      if (getPortalToken()) {
        try {
          setUser(await portalMe());
        } catch {
          setPortalToken(null);
        }
      }
      setLoading(false);
    })();
  }, []);

  const setSession = (token: string, u: PortalUser) => {
    setPortalToken(token);
    setUser(u);
  };
  const logout = () => {
    portalLogout().catch(() => {});
    setPortalToken(null);
    setUser(null);
  };

  return (
    <Ctx.Provider value={{ user, loading, setSession, logout }}>
      {children}
    </Ctx.Provider>
  );
}
