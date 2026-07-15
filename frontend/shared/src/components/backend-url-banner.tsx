import { useState, type FormEvent } from "react";
import { Settings, AlertCircle, ChevronDown, ChevronUp, CheckCircle } from "lucide-react";
import { isTauri, getApiBaseUrl, setBackendUrl } from "../api/backend-url";
import { cn } from "../lib/utils";

/**
 * Banner shown in Tauri desktop mode on the login page.
 *
 * Two states:
 * - WARNING (amber): user has NOT configured a custom backend URL; app falls
 *   back to http://localhost:8000 which works for local dev but not for
 *   enterprise deployment. Click to expand and configure.
 * - HIDDEN: user has explicitly configured a custom backend URL.
 *
 * In Web mode the Vite proxy handles routing, so this banner is hidden.
 */
export function BackendUrlBanner({ className }: { className?: string }) {
  const [expanded, setExpanded] = useState(false);
  const [url, setUrl] = useState("");

  if (!isTauri()) return null;

  const apiUrl = getApiBaseUrl();
  const hasCustomUrl =
    typeof window !== "undefined" &&
    typeof localStorage !== "undefined" &&
    !!localStorage.getItem("eaos.backend_url");

  if (hasCustomUrl) return null;

  const handleSave = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) return;
    setBackendUrl(trimmed);
    window.location.reload();
  };

  const isDefault = apiUrl.startsWith("http://localhost:8000");

  return (
    <div
      className={cn(
        "rounded-lg border text-foreground",
        isDefault
          ? "border-border bg-elevated"
          : "border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-100",
        className
      )}
    >
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
      >
        <div className="flex items-start gap-2.5">
          {isDefault ? (
            <CheckCircle className="mt-0.5 h-4 w-4 shrink-0 text-green-600" />
          ) : (
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          )}
          <div className="text-sm leading-snug">
            <p className="font-medium">
              {isDefault ? "后端连接：本地默认" : "需要配置后端连接"}
            </p>
            <p className="mt-0.5 text-xs opacity-80">
              {isDefault
                ? `当前连接 ${apiUrl}，点击修改为企业服务器地址`
                : "桌面应用尚未配置后端服务器地址，点击展开输入企业 EAOS 后端地址。"}
            </p>
          </div>
        </div>
        <span
          className={cn(
            "flex shrink-0 items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium",
            isDefault
              ? "bg-subtle hover:bg-border/50"
              : "bg-amber-900/10 hover:bg-amber-900/20 dark:bg-amber-100/10 dark:hover:bg-amber-100/20"
          )}
        >
          <Settings className="h-3.5 w-3.5" />
          {expanded ? (
            <ChevronUp className="h-3.5 w-3.5" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5" />
          )}
        </span>
      </button>

      {expanded && (
        <form
          onSubmit={handleSave}
          className={cn(
            "space-y-2 border-t px-4 py-3",
            isDefault
              ? "border-border"
              : "border-amber-200 dark:border-amber-900/50"
          )}
        >
          <label className="block text-xs font-medium">后端服务器地址</label>
          <input
            type="url"
            placeholder="http://localhost:8000"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            autoFocus
            className={cn(
              "h-10 w-full rounded-md border px-3 text-sm text-foreground placeholder:text-tertiary focus:outline-none focus:ring-1",
              isDefault
                ? "border-border bg-background focus:border-accent focus:ring-accent"
                : "border-amber-300 bg-white focus:border-amber-500 focus:ring-amber-500 dark:border-amber-900/50 dark:bg-amber-950/30"
            )}
          />
          <p className="text-xs opacity-80">
            不要包含{" "}
            <code
              className={cn(
                "rounded px-1",
                isDefault ? "bg-subtle" : "bg-amber-900/10"
              )}
            >
              /api
            </code>{" "}
            后缀。示例：
            <code
              className={cn(
                "ml-1 rounded px-1",
                isDefault ? "bg-subtle" : "bg-amber-900/10"
              )}
            >
              http://192.168.1.10:8000
            </code>
          </p>
          <button
            type="submit"
            disabled={!url.trim()}
            className={cn(
              "h-9 w-full rounded-md px-3 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
              isDefault
                ? "bg-accent text-white hover:bg-accent-strong"
                : "bg-amber-900 text-amber-50 hover:bg-amber-800 dark:bg-amber-100 dark:text-amber-950 dark:hover:bg-amber-200"
            )}
          >
            保存并刷新
          </button>
        </form>
      )}
    </div>
  );
}
