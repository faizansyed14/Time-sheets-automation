import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import {
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
  setSession: (token: string, user: AuthUser) => void;
  logout: () => void;
}

const Ctx = createContext<AuthCtx>({
  user: null,
  loading: true,
  isAdmin: false,
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
    setToken(null);
    setUser(null);
  };

  return (
    <Ctx.Provider value={{ user, loading, isAdmin: user?.role === "admin", setSession, logout }}>
      {children}
    </Ctx.Provider>
  );
}
