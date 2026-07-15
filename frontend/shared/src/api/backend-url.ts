/**
 * Backend URL resolver — supports both Web (Vite proxy) and Tauri (direct URL) modes.
 *
 * Resolution order:
 * 1. localStorage["eaos.backend_url"] — user-configured (settings page / banner)
 * 2. import.meta.env.VITE_API_BASE_URL — build-time override
 * 3. Tauri default: http://localhost:8000 (works out of the box for local dev)
 * 4. Web default: "/api" (Vite dev proxy rewrites to localhost:8000)
 *
 * IMPORTANT: The FastAPI backend has NO /api prefix. In Web mode the Vite proxy
 * adds then strips /api. In Tauri mode (no proxy), normalizeBackendUrl() strips
 * a trailing /api defensively so both forms work.
 */

/** True when running inside Tauri WebView. */
export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/**
 * Strip trailing slashes and a trailing /api suffix.
 * The backend has no /api prefix, so http://host:8000/api → http://host:8000.
 */
function normalizeBackendUrl(url: string): string {
  const normalized = url.replace(/\/+$/, "").replace(/\/api$/, "");
  return normalized || (isTauri() ? "http://localhost:8000" : "/api");
}

/** Default fallback when no URL is configured. */
function defaultBaseUrl(): string {
  return isTauri() ? "http://localhost:8000" : "/api";
}

/** Get the API base URL for openapi-fetch client. */
export function getApiBaseUrl(): string {
  if (typeof window !== "undefined" && typeof localStorage !== "undefined") {
    const stored = localStorage.getItem("eaos.backend_url");
    if (stored) return normalizeBackendUrl(stored);
  }
  const envUrl = import.meta.env.VITE_API_BASE_URL as string | undefined;
  if (envUrl) return normalizeBackendUrl(envUrl);
  return defaultBaseUrl();
}

/**
 * Get the backend root URL (no /api suffix) for SSE + file endpoints.
 * Returns "" in Web mode (same-origin, Vite proxy handles routing).
 */
export function getBackendRootUrl(): string {
  const api = getApiBaseUrl();
  if (api === "/api") return "";
  return api;
}

/** Persist backend URL to localStorage (settings page calls this). */
export function setBackendUrl(url: string): void {
  if (typeof window === "undefined" || typeof localStorage === "undefined") return;
  if (url) {
    localStorage.setItem("eaos.backend_url", url.replace(/\/$/, ""));
  } else {
    localStorage.removeItem("eaos.backend_url");
  }
}

/**
 * Resolve a backend-served path (e.g. "/uploads/...", "/invoke") to a fully
 * qualified URL. Absolute URLs (http://...) pass through unchanged. Relative
 * paths are prefixed with the backend root URL (empty in Web mode → Vite proxy).
 */
export function resolveBackendUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  const root = getBackendRootUrl();
  if (root === "") return path;
  const sep = path.startsWith("/") ? "" : "/";
  return `${root}${sep}${path}`;
}
