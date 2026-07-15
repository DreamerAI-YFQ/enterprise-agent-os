import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

/** Minimal user profile from GET /me (expanded in F1-T3) */
export interface EaosUser {
  id: string;
  tenant_id: string;
  email: string;
  name: string;
  role: "admin" | "super_admin" | "manager" | "employee" | "viewer";
  status?: string;
  preferences?: Record<string, unknown>;
}

interface AuthState {
  /** JWT access token — kept in memory (persisted to sessionStorage for UX) */
  token: string | null;
  user: EaosUser | null;
  setAuth: (token: string, user: EaosUser) => void;
  setToken: (token: string) => void;
  setUser: (user: EaosUser) => void;
  clear: () => void;
}

/**
 * Auth token store. Token is persisted to sessionStorage so a page refresh
 * doesn't lose the session, but it never touches localStorage (cleared when
 * the tab closes — a mild security improvement over localStorage).
 */
export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      setAuth: (token, user) => set({ token, user }),
      setToken: (token) => set({ token }),
      setUser: (user) => set({ user }),
      clear: () => set({ token: null, user: null }),
    }),
    {
      name: "eaos-auth",
      storage: createJSONStorage(() => sessionStorage),
      partialize: (s) => ({ token: s.token, user: s.user }),
    }
  )
);
