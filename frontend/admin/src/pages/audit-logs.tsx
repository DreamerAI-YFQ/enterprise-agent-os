import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import {
  Button,
  LoadingState,
  EmptyState,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  cn,
  toast,
} from "@eaos/shared";
import { Download, ScrollText, Eye } from "lucide-react";
import { relativeTime } from "../lib/relative-time";

interface AuditLog {
  id: number;
  actor_type: string;
  actor_id: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  detail: Record<string, unknown>;
  ip_address: string | null;
  created_at: string;
}

interface AuditLogResponse {
  items: AuditLog[];
  total: number;
  limit: number;
  offset: number;
}

const PAGE_SIZE = 20;

const ACTION_OPTIONS = [
  { value: "", label: "全部操作" },
  { value: "write", label: "write" },
  { value: "rollback", label: "rollback" },
  { value: "delegation", label: "delegation" },
  { value: "test_action", label: "test_action" },
];

const ACTOR_TYPE_LABELS: Record<string, string> = {
  user: "用户",
  agent: "Agent",
  system: "系统",
};

export default function AuditLogsPage() {
  const [action, setAction] = useState("");
  const [userId, setUserId] = useState("");
  const [startTime, setStartTime] = useState("");
  const [endTime, setEndTime] = useState("");
  const [page, setPage] = useState(0);
  const [detailLog, setDetailLog] = useState<AuditLog | null>(null);

  const query = useQuery({
    queryKey: ["admin", "audit-logs", { action, userId, startTime, endTime, page }],
    queryFn: async () => {
      const query: Record<string, string | number> = {
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      };
      if (action) query.action = action;
      if (userId) query.user_id = userId;
      if (startTime) query.start_time = new Date(startTime).toISOString();
      if (endTime) query.end_time = new Date(endTime).toISOString();

      const { data, error } = await apiClient.GET("/admin/audit-logs", {
        params: { query },
      });
      if (error || !data) return { items: [], total: 0, limit: PAGE_SIZE, offset: 0 } as AuditLogResponse;
      return data as unknown as AuditLogResponse;
    },
  });

  const logs = query.data?.items ?? [];
  const total = query.data?.total ?? 0;
  const totalPages = Math.ceil(total / PAGE_SIZE);

  const handleExport = async () => {
    try {
      const query: Record<string, string> = {};
      if (action) query.action = action;
      if (userId) query.user_id = userId;
      if (startTime) query.start_time = new Date(startTime).toISOString();
      if (endTime) query.end_time = new Date(endTime).toISOString();

      const { data, error } = await apiClient.GET("/admin/audit-logs/export", {
        params: { query },
        parseAs: "text",
      });
      if (error || !data) throw new Error("Export failed");
      const blob = new Blob([data], { type: "text/csv" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "audit-logs.csv";
      a.click();
      URL.revokeObjectURL(url);
      toast.show({ title: "导出成功", variant: "success" });
    } catch {
      toast.show({ title: "导出失败", variant: "danger" });
    }
  };

  const handleFilterReset = () => {
    setAction("");
    setUserId("");
    setStartTime("");
    setEndTime("");
    setPage(0);
  };

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="border-b border-border-subtle px-8 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">审计日志</h1>
            <p className="mt-1 text-sm text-secondary">
              {total > 0 ? `共 ${total} 条记录` : "暂无记录"}
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={handleExport} disabled={total === 0}>
            <Download className="h-3.5 w-3.5" />
            导出 CSV
          </Button>
        </div>
      </div>

      {/* Filters */}
      <div className="border-b border-border-subtle px-8 py-4">
        <div className="flex flex-wrap items-center gap-3">
          <select
            value={action}
            onChange={(e) => { setAction(e.target.value); setPage(0); }}
            className="rounded-md border border-border bg-elevated px-3 py-1.5 text-sm text-foreground outline-none focus:border-accent"
          >
            {ACTION_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
          <input
            type="text"
            placeholder="用户 ID (UUID)"
            value={userId}
            onChange={(e) => { setUserId(e.target.value); setPage(0); }}
            className="w-64 rounded-md border border-border bg-elevated px-3 py-1.5 text-sm text-foreground placeholder:text-tertiary outline-none focus:border-accent"
          />
          <div className="flex items-center gap-1.5">
            <input
              type="datetime-local"
              value={startTime}
              onChange={(e) => { setStartTime(e.target.value); setPage(0); }}
              className="rounded-md border border-border bg-elevated px-3 py-1.5 text-sm text-foreground outline-none focus:border-accent"
            />
            <span className="text-xs text-tertiary">—</span>
            <input
              type="datetime-local"
              value={endTime}
              onChange={(e) => { setEndTime(e.target.value); setPage(0); }}
              className="rounded-md border border-border bg-elevated px-3 py-1.5 text-sm text-foreground outline-none focus:border-accent"
            />
          </div>
          {(action || userId || startTime || endTime) && (
            <button
              onClick={handleFilterReset}
              className="text-xs text-secondary hover:text-foreground transition-colors"
            >
              清除筛选
            </button>
          )}
        </div>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-y-auto px-8 pb-8">
        {query.isLoading ? (
          <LoadingState />
        ) : logs.length === 0 ? (
          <EmptyState
            icon={ScrollText}
            title="暂无审计日志"
            description="系统操作记录将出现在这里"
          />
        ) : (
          <div className="overflow-hidden rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead className="bg-subtle/50">
                <tr className="text-left text-xs text-secondary">
                  <th className="px-4 py-2.5 font-medium">时间</th>
                  <th className="px-4 py-2.5 font-medium">操作者</th>
                  <th className="px-4 py-2.5 font-medium">操作</th>
                  <th className="px-4 py-2.5 font-medium">资源类型</th>
                  <th className="px-4 py-2.5 font-medium">IP</th>
                  <th className="px-4 py-2.5 font-medium text-right">详情</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {logs.map((log) => (
                  <tr
                    key={log.id}
                    className="cursor-pointer transition-colors hover:bg-subtle/30"
                    onClick={() => setDetailLog(log)}
                  >
                    <td className="px-4 py-2.5 text-secondary whitespace-nowrap">
                      {relativeTime(log.created_at)}
                    </td>
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        <span className="rounded-full bg-subtle px-1.5 py-0.5 text-xs text-tertiary">
                          {ACTOR_TYPE_LABELS[log.actor_type] ?? log.actor_type}
                        </span>
                        <span className="text-xs text-tertiary font-mono">
                          {log.actor_id.substring(0, 8)}...
                        </span>
                      </div>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="font-mono text-xs text-foreground">{log.action}</span>
                    </td>
                    <td className="px-4 py-2.5 text-secondary">
                      {log.resource_type || "—"}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-tertiary font-mono">
                      {log.ip_address || "—"}
                    </td>
                    <td className="px-4 py-2.5 text-right">
                      <button
                        className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
                        onClick={(e) => { e.stopPropagation(); setDetailLog(log); }}
                      >
                        <Eye className="h-3 w-3" />
                        查看
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="mt-4 flex items-center justify-between">
            <p className="text-xs text-tertiary">
              第 {page + 1} / {totalPages} 页 · 共 {total} 条
            </p>
            <div className="flex gap-1">
              <Button
                variant="outline"
                size="sm"
                disabled={page === 0}
                onClick={() => setPage(page - 1)}
              >
                上一页
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= totalPages - 1}
                onClick={() => setPage(page + 1)}
              >
                下一页
              </Button>
            </div>
          </div>
        )}
      </div>

      {/* Detail Dialog */}
      <Dialog open={detailLog !== null} onOpenChange={(open) => !open && setDetailLog(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>审计日志详情</DialogTitle>
          </DialogHeader>
          {detailLog && (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3 text-sm">
                <DetailField label="ID" value={String(detailLog.id)} />
                <DetailField label="时间" value={new Date(detailLog.created_at).toLocaleString("zh-CN")} />
                <DetailField label="操作者类型" value={ACTOR_TYPE_LABELS[detailLog.actor_type] ?? detailLog.actor_type} />
                <DetailField label="操作者 ID" value={detailLog.actor_id} mono />
                <DetailField label="操作" value={detailLog.action} mono />
                <DetailField label="资源类型" value={detailLog.resource_type || "—"} />
                <DetailField label="资源 ID" value={detailLog.resource_id || "—"} mono />
                <DetailField label="IP 地址" value={detailLog.ip_address || "—"} mono />
              </div>
              <div>
                <p className="mb-1 text-xs font-medium text-secondary">Detail Payload</p>
                <pre className="max-h-60 overflow-auto rounded-md bg-subtle p-3 text-xs text-foreground">
                  {JSON.stringify(detailLog.detail, null, 2)}
                </pre>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

function DetailField({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <p className="text-xs text-tertiary">{label}</p>
      <p className={cn("mt-0.5 text-sm text-foreground break-all", mono && "font-mono text-xs")}>
        {value}
      </p>
    </div>
  );
}
