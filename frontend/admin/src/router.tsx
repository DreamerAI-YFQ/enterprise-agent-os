import { lazy, Suspense, type ReactNode } from "react";
import {
  createBrowserRouter,
  Navigate,
  RouterProvider,
} from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  AdminRoute,
  AuthProvider,
  PublicOnlyRoute,
  Spinner,
  ErrorBoundary,
  OnboardingGuide,
  Toaster,
} from "@eaos/shared";
import { AppLayout } from "./layouts/app-layout";
import Login from "./pages/login";

// Lazy-load all page components for code splitting
const Dashboard = lazy(() => import("./pages/dashboard"));
const Agents = lazy(() => import("./pages/agents"));
const Approvals = lazy(() => import("./pages/approvals"));
const Skills = lazy(() => import("./pages/skills"));
const Documents = lazy(() => import("./pages/documents"));
const Ontology = lazy(() => import("./pages/ontology"));
const Contributions = lazy(() => import("./pages/contributions"));
const BiData = lazy(() => import("./pages/bi-data"));
const BiSql = lazy(() => import("./pages/bi-sql"));
const BiQuery = lazy(() => import("./pages/bi-query"));
const BiMetrics = lazy(() => import("./pages/bi-metrics"));
const MonitorExecutions = lazy(() => import("./pages/monitor-executions"));
const MonitorTraces = lazy(() => import("./pages/monitor-traces"));
const MonitorDashboard = lazy(() => import("./pages/monitor-dashboard"));
const Workflows = lazy(() => import("./pages/workflows"));
const Promotions = lazy(() => import("./pages/promotions"));
const Models = lazy(() => import("./pages/models"));
const McpConnectors = lazy(() => import("./pages/mcp-connectors"));
const Triggers = lazy(() => import("./pages/triggers"));
const Users = lazy(() => import("./pages/users"));
const Departments = lazy(() => import("./pages/departments"));
const Roles = lazy(() => import("./pages/roles"));
const Tenants = lazy(() => import("./pages/tenants"));
const Sso = lazy(() => import("./pages/sso"));
const Memory = lazy(() => import("./pages/memory"));
const Notifications = lazy(() => import("./pages/notifications"));
const AuditLogs = lazy(() => import("./pages/audit-logs"));
const DataManagement = lazy(() => import("./pages/data-management"));
const SafetyCases = lazy(() => import("./pages/safety-cases"));
const ReportTemplates = lazy(() => import("./pages/report-templates"));
const Plugins = lazy(() => import("./pages/plugins"));
const Settings = lazy(() => import("./pages/settings"));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

function PageSuspense({ children }: { children: ReactNode }) {
  return <Suspense fallback={<div className="flex h-full items-center justify-center"><Spinner /></div>}>{children}</Suspense>;
}

export const router = createBrowserRouter([
  {
    path: "/",
    element: <Navigate to="/admin" replace />,
  },
  {
    path: "/login",
    element: (
      <PublicOnlyRoute>
        <Login />
      </PublicOnlyRoute>
    ),
  },
  {
    path: "/admin",
    element: (
      <AdminRoute>
        <AppLayout />
      </AdminRoute>
    ),
    children: [
      { index: true, element: <PageSuspense><Dashboard /></PageSuspense> },
      { path: "agents", element: <PageSuspense><Agents /></PageSuspense> },
      { path: "approvals", element: <PageSuspense><Approvals /></PageSuspense> },
      { path: "workflows", element: <PageSuspense><Workflows /></PageSuspense> },
      { path: "skills", element: <PageSuspense><Skills /></PageSuspense> },
      { path: "documents", element: <PageSuspense><Documents /></PageSuspense> },
      { path: "ontology", element: <PageSuspense><Ontology /></PageSuspense> },
      { path: "contributions", element: <PageSuspense><Contributions /></PageSuspense> },
      { path: "promotions", element: <PageSuspense><Promotions /></PageSuspense> },
      { path: "bi/data", element: <PageSuspense><BiData /></PageSuspense> },
      { path: "bi/sql", element: <PageSuspense><BiSql /></PageSuspense> },
      { path: "bi/query", element: <PageSuspense><BiQuery /></PageSuspense> },
      { path: "bi/metrics", element: <PageSuspense><BiMetrics /></PageSuspense> },
      { path: "monitor/executions", element: <PageSuspense><MonitorExecutions /></PageSuspense> },
      { path: "traces", element: <PageSuspense><MonitorTraces /></PageSuspense> },
      { path: "monitor/dashboard", element: <PageSuspense><MonitorDashboard /></PageSuspense> },
      { path: "models", element: <PageSuspense><Models /></PageSuspense> },
      { path: "mcp-connectors", element: <PageSuspense><McpConnectors /></PageSuspense> },
      { path: "triggers", element: <PageSuspense><Triggers /></PageSuspense> },
      { path: "users", element: <PageSuspense><Users /></PageSuspense> },
      { path: "departments", element: <PageSuspense><Departments /></PageSuspense> },
      { path: "roles", element: <PageSuspense><Roles /></PageSuspense> },
      { path: "tenants", element: <PageSuspense><Tenants /></PageSuspense> },
      { path: "sso", element: <PageSuspense><Sso /></PageSuspense> },
      { path: "memory", element: <PageSuspense><Memory /></PageSuspense> },
      { path: "notifications", element: <PageSuspense><Notifications /></PageSuspense> },
      { path: "audit-logs", element: <PageSuspense><AuditLogs /></PageSuspense> },
      { path: "data-management", element: <PageSuspense><DataManagement /></PageSuspense> },
      { path: "safety-cases", element: <PageSuspense><SafetyCases /></PageSuspense> },
      { path: "report-templates", element: <PageSuspense><ReportTemplates /></PageSuspense> },
      { path: "plugins", element: <PageSuspense><Plugins /></PageSuspense> },
      { path: "settings", element: <PageSuspense><Settings /></PageSuspense> },
    ],
  },
  { path: "*", element: <Navigate to="/admin" replace /> },
]);

export function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <RouterProvider router={router} />
          <OnboardingGuide />
          <Toaster />
        </AuthProvider>
      </QueryClientProvider>
    </ErrorBoundary>
  );
}
