import { useState } from "react";
import type { ChangeEvent } from "react";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import {
  getApiBaseUrl,
  setBackendUrl,
  isTauri,
} from "../api/backend-url";
import { cn } from "../lib/utils";

/**
 * Backend URL configuration section — shared between employee + admin settings.
 * Lets the user override the API base URL (required for Tauri desktop mode
 * where there's no Vite proxy). In Web mode the default "/api" works without
 * configuration.
 */
export function BackendUrlSettings({ className }: { className?: string }) {
  const current = getApiBaseUrl();
  const initial = current === "/api" ? "" : current;
  const [url, setUrl] = useState(initial);
  const [saved, setSaved] = useState(false);

  const handleSave = () => {
    setBackendUrl(url.trim());
    setSaved(true);
    window.setTimeout(() => setSaved(false), 2000);
  };

  const handleReset = () => {
    setBackendUrl("");
    setUrl("");
    setSaved(true);
    window.setTimeout(() => setSaved(false), 2000);
  };

  const desktop = isTauri();

  return (
    <section
      className={cn(
        "rounded-md border border-border bg-elevated p-6 shadow-sm",
        className
      )}
    >
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-medium text-foreground">后端连接</h2>
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" onClick={handleReset}>
            恢复默认
          </Button>
          <Button size="sm" onClick={handleSave} disabled={url === initial}>
            保存
          </Button>
        </div>
      </div>
      <p className="mt-1 text-xs text-tertiary">
        {desktop
          ? "桌面应用需要配置后端服务器地址才能连接 API"
          : "Web 模式默认使用相对路径 /api，通常无需配置"}
      </p>

      <div className="mt-4 space-y-2">
        <label className="text-xs font-medium text-secondary">
          后端服务器地址
        </label>
        <Input
          type="url"
          placeholder="http://localhost:8000"
          value={url}
          onChange={(e: ChangeEvent<HTMLInputElement>) => setUrl(e.target.value)}
        />
        <p className="text-xs text-tertiary">
          不要包含 <code className="rounded bg-subtle px-1">/api</code> 后缀。示例：
          <code className="ml-1 rounded bg-subtle px-1">http://192.168.1.10:8000</code>
        </p>
      </div>

      {saved && (
        <p className="mt-3 text-sm text-green-600">
          已保存，刷新页面后生效
        </p>
      )}
    </section>
  );
}
