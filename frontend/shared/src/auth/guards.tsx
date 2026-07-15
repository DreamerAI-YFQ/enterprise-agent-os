import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "./use-auth";

interface GuardProps {
  children: ReactNode;
}

/**
 * Requires authentication. Redirects to /login when unauthenticated.
 * Used for all /app/* and /admin/* routes.
 */
export function ProtectedRoute({ children }: GuardProps) {
  const { isAuthenticated } = useAuth();
  const location = useLocation();

  if (!isAuthenticated) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  return <>{children}</>;
}

/**
 * Requires admin or super_admin role. Unauthenticated users are redirected
 * to /login. Authenticated non-admins are redirected to /app.
 * Used for /admin/* routes.
 */
export function AdminRoute({ children }: GuardProps) {
  const { isAuthenticated, user } = useAuth();

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }
  if (user?.role !== "admin" && user?.role !== "super_admin") {
    return <Navigate to="/app" replace />;
  }
  return <>{children}</>;
}

/**
 * Public-only route (login page). Redirects authenticated users away.
 * Admins/super_admins → /admin, employees → /app.
 */
export function PublicOnlyRoute({ children }: GuardProps) {
  const { isAuthenticated, user } = useAuth();

  if (isAuthenticated) {
    const isAdmin = user?.role === "admin" || user?.role === "super_admin";
    return <Navigate to={isAdmin ? "/admin" : "/app"} replace />;
  }
  return <>{children}</>;
}
