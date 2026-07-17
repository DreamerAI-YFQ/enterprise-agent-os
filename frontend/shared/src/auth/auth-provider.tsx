import {
  createContext,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { apiClient, useAuthStore, type EaosUser } from "../api/client";

interface LoginResponse {
  access_token: string;
  token_type: string;
  user?: Record<string, string> | null;
}

export interface AuthContextValue {
  user: EaosUser | null;
  token: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (tenantSlug: string, email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

const TOKEN_REFRESH_INTERVAL_MS = 25 * 60 * 1000; // 25 min

export function AuthProvider({ children }: { children: ReactNode }) {
  const token = useAuthStore((s) => s.token);
  const user = useAuthStore((s) => s.user);
  const setAuth = useAuthStore((s) => s.setAuth);
  const clear = useAuthStore((s) => s.clear);
  const [isLoading, setIsLoading] = useState(false);

  const fetchMe = useCallback(async (): Promise<EaosUser | null> => {
    const { data, error } = await apiClient.GET("/me", {});
    if (error || !data) return null;
    return data as unknown as EaosUser;
  }, []);

  const login = useCallback(
    async (tenantSlug: string, email: string, password: string) => {
      setIsLoading(true);
      try {
        const { data, error } = await apiClient.POST("/auth/login", {
          body: { tenant_slug: tenantSlug, email, password },
        });
        if (error || !data) {
          throw new Error(
            (error as { detail?: string } | undefined)?.detail ??
              "登录失败，请检查邮箱"
          );
        }
        const resp = data as unknown as LoginResponse;
        setAuth(resp.access_token, {
          id: resp.user?.id ?? "",
          tenant_id: resp.user?.tenant_id ?? "",
          email,
          name: resp.user?.name ?? email,
          role: (resp.user?.role as EaosUser["role"]) ?? "employee",
        });
        // Fetch full profile from /me
        const fullUser = await fetchMe();
        if (fullUser) {
          useAuthStore.getState().setUser(fullUser);
        }
      } finally {
        setIsLoading(false);
      }
    },
    [fetchMe, setAuth]
  );

  const logout = useCallback(async () => {
    try {
      await apiClient.POST("/auth/logout", {});
    } catch {
      // Stateless logout — ignore network errors
    }
    clear();
  }, [clear]);

  const refresh = useCallback(async () => {
    if (!useAuthStore.getState().token) return;
    const { data, error } = await apiClient.POST("/auth/refresh", {});
    if (error || !data) {
      clear();
      return;
    }
    const resp = data as unknown as LoginResponse;
    useAuthStore.getState().setToken(resp.access_token);
  }, [clear]);

  // Proactive token refresh
  useEffect(() => {
    if (!token) return;
    const id = setInterval(refresh, TOKEN_REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, [token, refresh]);

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      token,
      isAuthenticated: !!token,
      isLoading,
      login,
      logout,
      refresh,
    }),
    [user, token, isLoading, login, logout, refresh]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
