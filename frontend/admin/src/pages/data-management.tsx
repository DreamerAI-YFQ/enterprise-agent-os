import { useRef, useState } from "react";
import { apiClient } from "@eaos/shared/api";
import { Button, toast, cn, Spinner } from "@eaos/shared";
import {
  Download,
  Upload,
  Users,
  Brain,
  FileText,
  MessageSquare,
  CheckCircle2,
  AlertCircle,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

type Resource = "users" | "memory" | "knowledge" | "sessions";
type Format = "json" | "csv";

interface ResourceMeta {
  key: Resource;
  label: string;
  description: string;
  icon: LucideIcon;
  importable: boolean;
}

const RESOURCES: ResourceMeta[] = [
  {
    key: "users",
    label: "用户",
    description: "用户账号、角色、状态、偏好",
    icon: Users,
    importable: true,
  },
  {
    key: "memory",
    label: "记忆",
    description: "组织记忆（事实/偏好/程序性）",
    icon: Brain,
    importable: true,
  },
  {
    key: "knowledge",
    label: "知识",
    description: "知识库文档（含分块内容）",
    icon: FileText,
    importable: true,
  },
  {
    key: "sessions",
    label: "会话",
    description: "对话会话元数据（不含消息体）",
    icon: MessageSquare,
    importable: false,
  },
];

interface ImportSummary {
  resource: string;
  total: number;
  created: number;
  updated: number;
  skipped: number;
  errors: string[];
}

export default function DataManagementPage() {
  const [format, setFormat] = useState<Format>("json");
  const [importMode, setImportMode] = useState<"skip" | "upsert">("skip");
  const [exporting, setExporting] = useState<Resource | null>(null);
  const [importing, setImporting] = useState<Resource | null>(null);
  const [importSummary, setImportSummary] = useState<ImportSummary | null>(null);
  const fileInputRefs = useRef<Record<Resource, HTMLInputElement | null>>({
    users: null,
    memory: null,
    knowledge: null,
    sessions: null,
  });

  const handleExport = async (resource: Resource) => {
    setExporting(resource);
    try {
      const { data, error } = await apiClient.GET(
        "/admin/export/{resource}",
        { params: { path: { resource }, query: { format } }, parseAs: "text" },
      );
      if (error || !data) throw new Error("导出失败");
      const mime = format === "json" ? "application/json" : "text/csv";
      const blob = new Blob([data], { type: mime });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${resource}.${format}`;
      a.click();
      URL.revokeObjectURL(url);
      toast.show({ title: "导出成功", description: `${resource}.${format} 已下载`, variant: "success" });
    } catch {
      toast.show({ title: "导出失败", variant: "danger" });
    } finally {
      setExporting(null);
    }
  };

  const handleImportFile = (resource: Resource) => {
    fileInputRefs.current[resource]?.click();
  };

  const handleFileChange = async (
    resource: Resource,
    event: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const file = event.target.files?.[0];
    if (!file) return;
    event.target.value = ""; // reset for re-import

    setImporting(resource);
    setImportSummary(null);
    try {
      const text = await file.text();
      let items: unknown[];
      try {
        const parsed = JSON.parse(text);
        items = Array.isArray(parsed) ? parsed : (parsed.items ?? []);
      } catch {
        throw new Error("文件不是有效的 JSON");
      }
      if (!Array.isArray(items) || items.length === 0) {
        throw new Error("JSON 中未找到 items 数组");
      }
      const { data, error } = await apiClient.POST(
        "/admin/import/{resource}",
        {
          params: { path: { resource } },
          body: { items: items as never, mode: importMode },
        },
      );
      if (error || !data) throw new Error("导入失败");
      setImportSummary(data as unknown as ImportSummary);
      const summary = data as unknown as ImportSummary;
      toast.show({
        title: "导入完成",
        description: `新增 ${summary.created} · 更新 ${summary.updated} · 跳过 ${summary.skipped}`,
        variant: summary.errors.length > 0 ? "warning" : "success",
      });
    } catch (e) {
      toast.show({
        title: "导入失败",
        description: e instanceof Error ? e.message : undefined,
        variant: "danger",
      });
    } finally {
      setImporting(null);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <h1 className="text-2xl font-semibold text-foreground">数据管理</h1>
        <p className="mt-1 text-sm text-secondary">
          导出/导入平台数据，支持 JSON 与 CSV 格式
        </p>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6">
        <div className="mx-auto max-w-4xl space-y-6">
          {/* Format selector */}
          <section className="rounded-md border border-border bg-elevated p-4 shadow-sm">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-sm font-medium text-foreground">导出格式</h2>
                <p className="mt-0.5 text-xs text-tertiary">
                  JSON 保留完整结构，CSV 适合在 Excel 中查看
                </p>
              </div>
              <div className="flex items-center gap-1 rounded-md bg-subtle p-0.5">
                {(["json", "csv"] as Format[]).map((f) => (
                  <button
                    key={f}
                    onClick={() => setFormat(f)}
                    className={cn(
                      "rounded-sm px-3 py-1 text-xs font-medium uppercase transition-colors",
                      format === f
                        ? "bg-elevated text-foreground shadow-sm"
                        : "text-secondary hover:text-foreground",
                    )}
                  >
                    {f}
                  </button>
                ))}
              </div>
            </div>
          </section>

          {/* Export cards */}
          <section>
            <h2 className="mb-3 text-sm font-medium text-foreground">数据导出</h2>
            <div className="grid grid-cols-2 gap-4">
              {RESOURCES.map((r) => (
                <div
                  key={r.key}
                  className="rounded-md border border-border bg-elevated p-4 shadow-sm"
                >
                  <div className="flex items-start justify-between">
                    <div className="flex items-start gap-3">
                      <div className="flex h-10 w-10 items-center justify-center rounded-md bg-accent/10 text-accent">
                        <r.icon className="h-5 w-5" strokeWidth={1.75} />
                      </div>
                      <div>
                        <p className="text-sm font-medium text-foreground">{r.label}</p>
                        <p className="mt-0.5 text-xs text-tertiary">{r.description}</p>
                      </div>
                    </div>
                  </div>
                  <div className="mt-3 flex justify-end">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => handleExport(r.key)}
                      disabled={exporting === r.key}
                    >
                      {exporting === r.key ? (
                        <Spinner className="h-3.5 w-3.5" />
                      ) : (
                        <Download className="h-3.5 w-3.5" />
                      )}
                      导出 {format.toUpperCase()}
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </section>

          {/* Import section */}
          <section>
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-medium text-foreground">数据导入</h2>
              <div className="flex items-center gap-1 rounded-md bg-subtle p-0.5">
                {(["skip", "upsert"] as const).map((m) => (
                  <button
                    key={m}
                    onClick={() => setImportMode(m)}
                    className={cn(
                      "rounded-sm px-2.5 py-1 text-xs font-medium transition-colors",
                      importMode === m
                        ? "bg-elevated text-foreground shadow-sm"
                        : "text-secondary hover:text-foreground",
                    )}
                  >
                    {m === "skip" ? "跳过已存在" : "更新已存在"}
                  </button>
                ))}
              </div>
            </div>
            <div className="grid grid-cols-3 gap-4">
              {RESOURCES.filter((r) => r.importable).map((r) => (
                <div
                  key={r.key}
                  className="rounded-md border border-border bg-elevated p-4 shadow-sm"
                >
                  <div className="flex items-center gap-3">
                    <div className="flex h-9 w-9 items-center justify-center rounded-md bg-accent/10 text-accent">
                      <r.icon className="h-4 w-4" strokeWidth={1.75} />
                    </div>
                    <div>
                      <p className="text-sm font-medium text-foreground">{r.label}</p>
                      <p className="text-xs text-tertiary">仅 JSON</p>
                    </div>
                  </div>
                  <div className="mt-3 flex justify-end">
                    <input
                      ref={(el) => {
                        fileInputRefs.current[r.key] = el;
                      }}
                      type="file"
                      accept="application/json,.json"
                      className="hidden"
                      onChange={(e) => handleFileChange(r.key, e)}
                    />
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => handleImportFile(r.key)}
                      disabled={importing === r.key}
                    >
                      {importing === r.key ? (
                        <Spinner className="h-3.5 w-3.5" />
                      ) : (
                        <Upload className="h-3.5 w-3.5" />
                      )}
                      上传 JSON
                    </Button>
                  </div>
                </div>
              ))}
            </div>
            <p className="mt-2 text-xs text-tertiary">
              导入文件需为 JSON 格式，包含 <code className="rounded bg-subtle px-1">items</code> 数组。导出文件可直接作为导入模板。
            </p>
          </section>

          {/* Last import summary */}
          {importSummary && (
            <section className="rounded-md border border-border bg-elevated p-4 shadow-sm">
              <div className="flex items-center gap-2">
                {importSummary.errors.length > 0 ? (
                  <AlertCircle className="h-4 w-4 text-warning" />
                ) : (
                  <CheckCircle2 className="h-4 w-4 text-success" />
                )}
                <h3 className="text-sm font-medium text-foreground">
                  上次导入结果 · {importSummary.resource}
                </h3>
              </div>
              <div className="mt-3 grid grid-cols-4 gap-3 text-center">
                <div>
                  <p className="text-xs text-tertiary">总数</p>
                  <p className="text-lg font-semibold text-foreground">{importSummary.total}</p>
                </div>
                <div>
                  <p className="text-xs text-tertiary">新增</p>
                  <p className="text-lg font-semibold text-success">{importSummary.created}</p>
                </div>
                <div>
                  <p className="text-xs text-tertiary">更新</p>
                  <p className="text-lg font-semibold text-accent">{importSummary.updated}</p>
                </div>
                <div>
                  <p className="text-xs text-tertiary">跳过</p>
                  <p className="text-lg font-semibold text-secondary">{importSummary.skipped}</p>
                </div>
              </div>
              {importSummary.errors.length > 0 && (
                <div className="mt-3 rounded-md bg-warning/5 p-3">
                  <p className="text-xs font-medium text-warning">错误明细</p>
                  <ul className="mt-1 space-y-0.5">
                    {importSummary.errors.slice(0, 10).map((err, i) => (
                      <li key={i} className="text-xs text-secondary">{err}</li>
                    ))}
                    {importSummary.errors.length > 10 && (
                      <li className="text-xs text-tertiary">
                        ...还有 {importSummary.errors.length - 10} 条
                      </li>
                    )}
                  </ul>
                </div>
              )}
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
