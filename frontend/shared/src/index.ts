// Barrel export for @eaos/shared

// UI components
export { Button, buttonVariants, type ButtonProps } from "./components/ui/button";
export { Input, type InputProps } from "./components/ui/input";
export {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from "./components/ui/card";
export { Badge, badgeVariants, type BadgeProps } from "./components/ui/badge";
export {
  Dialog,
  DialogPortal,
  DialogOverlay,
  DialogTrigger,
  DialogClose,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
} from "./components/ui/dialog";
export {
  ToastProvider,
  ToastViewport,
  Toast,
  ToastTitle,
  ToastDescription,
  ToastClose,
  type ToastVariant,
} from "./components/ui/toast";
export { Toaster } from "./components/ui/toaster";
export { EmptyState, type EmptyStateProps } from "./components/ui/empty-state";
export { Pagination, type PaginationProps } from "./components/ui/pagination";
export { SearchInput, type SearchInputProps } from "./components/ui/search-input";
export { FilterBar, type FilterBarProps, type FilterOption } from "./components/ui/filter-bar";
export { LoadingState, type LoadingStateProps } from "./components/ui/loading-state";
export { Avatar } from "./components/ui/avatar";
export { Spinner } from "./components/ui/spinner";
export { BackendUrlSettings } from "./components/backend-url-settings";
export { BackendUrlBanner } from "./components/backend-url-banner";
export { NotificationBell } from "./components/notification-bell";
export { ErrorBoundary } from "./components/error-boundary";
export { OnboardingGuide } from "./components/onboarding-guide";
export { PreferencesForm } from "./components/preferences-form";
export { LanguageSwitcher, type LanguageSwitcherProps } from "./components/language-switcher";

// Lib
export { cn } from "./lib/utils";
export { toast, useToastStore, type ToastItem } from "./lib/toast-store";
export {
  setTheme,
  getStoredTheme,
  initTheme,
  watchSystemTheme,
  type ThemePreference,
} from "./lib/theme";

// i18n
export {
  i18n,
  initI18n,
  getStoredLocale,
  setLocale,
  type AppLocale,
} from "./i18n";
export { useTranslation, Trans, withTranslation } from "react-i18next";

// Auth
export { AuthProvider, useAuth, ProtectedRoute, AdminRoute, PublicOnlyRoute } from "./auth";

// Theme
export { eaosTheme } from "./design/theme";

// AgentEvent types & SSE streaming
export {
  type AgentEvent,
  type AgentEventType,
  type AttachmentRef,
  type InvokeRequest,
  type ResumeRequest,
  parseApprovalId,
} from "./types/agent-event";
export { streamInvoke, streamResume } from "./hooks/stream-invoke";
export { useClientPagination, type UseClientPaginationOptions } from "./hooks/use-client-pagination";
export { uploadFile } from "./api/upload";
export {
  getApiBaseUrl,
  getBackendRootUrl,
  setBackendUrl,
  resolveBackendUrl,
  isTauri,
} from "./api/backend-url";
