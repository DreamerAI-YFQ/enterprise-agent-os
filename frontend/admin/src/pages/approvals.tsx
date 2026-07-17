import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import { Button, Spinner, cn, toast } from "@eaos/shared";
import {
  ShieldCheck,
  Check,
  X,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  ChevronUp,
  Wrench,
  Database,
  AlertTriangle,
} from "lucide-react";
import { relativeTime } from "../lib/relative-time";

interface Approval {
  id: string;
  tenant_id: string;
  agent_id: string;
  skill_id: string | null;
  session_id: string;
  reason: string;
  status: string;
  requested_by: string;
  decided_by: string | null;
  decided_at: string | null;
  created_at: string;
  tool_name: string | null;
  resource: string | null;
  operation: string | null;
  risk_level: string | null;
  intent_data: Record<string, unknown> | null;
}

interface ApprovalListResponse {
  items: Approval[];
  total: number;
  limit: number;
  offset: number;
}

type StatusFilter =
  | "all"
  | "pending"
  | "approved"
  | "executing"
  | "consumed"
  | "rejected"
  | "expired";

const PAGE_SIZE = 10;

const STATUS_TABS: { key: StatusFilter; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "pending", label: "待审批" },
  { key: "approved", label: "已批准" },
  { key: "executing", label: "执行中" },
  { key: "consumed", label: "已执行" },
  { key: "rejected", label: "已驳回" },
  { key: "expired", label: "已过期" },
];

const REASON_LABELS: Record<string, string> = {
  high_risk_write: "高风险写入",
  high_risk: "高风险操作",
  cost_threshold: "成本阈值",
  quality_degraded: "质量下降",
};

const RISK_CONFIG: Record<string, { label: string; color: string; icon: typeof AlertTriangle }> = {
  high: { label: "高", color: "bg-danger/10 text-danger", icon: AlertTriangle },
  medium: { label: "中", color: "bg-warning/10 text-warning", icon: AlertTriangle },
  low: { label: "低", color: "bg-success/10 text-success", icon: AlertTriangle },
};

const OPERATION_LABELS: Record<string, string> = {
  create: "创建",
  update: "更新",
  delete: "删除",
};

function statusLabel(status: string): string {
  if (status === "pending") return "待审批";
  if (status === "approved") return "已批准";
  if (status === "executing") return "执行中";
  if (status === "consumed") return "已执行";
  if (status === "rejected") return "已驳回";
  if (status === "expired") return "已过期";
  return status;
}

function statusBadgeClass(status: string): string {
  if (status === "pending") return "bg-warning/10 text-warning";
  if (status === "approved") return "bg-success/10 text-success";
  if (status === "executing") return "bg-accent/10 text-accent";
  if (status === "consumed") return "bg-success/10 text-success";
  if (status === "expired") return "bg-subtle text-secondary";
  return "bg-danger/10 text-danger";
}

export default function ApprovalsPage() {
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("pending");
  const [page, setPage] = useState(0);
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  const offset = page * PAGE_SIZE;
  const queryStatus = statusFilter === "all" ? undefined : statusFilter;

  const query = useQuery({
    queryKey: ["admin", "approvals", statusFilter, page],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/approvals", {
        params: {
          query: {
            status: queryStatus,
            limit: PAGE_SIZE,
            offset,
          },
        },
      });
      if (error || !data) return { items: [], total: 0, limit: PAGE_SIZE, offset } as ApprovalListResponse;
      return data as unknown as ApprovalListResponse;
    },
    refetchInterval: 30_000,
  });

  const approveMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiClient.POST("/admin/approvals/{approval_id}/approve", {
        params: { path: { approval_id: id } },
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "approvals"] });
      toast.show({ title: "已批准", variant: "success" });
    },
  });

  const rejectMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiClient.POST("/admin/approvals/{approval_id}/reject", {
        params: { path: { approval_id: id } },
        body: { reason: rejectReason },
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "approvals"] });
      setRejectingId(null);
      setRejectReason("");
      toast.show({ title: "已驳回", variant: "success" });
    },
  });

  const response = query.data;
  const approvals = response?.items ?? [];
  const total = response?.total ?? 0;
  const hasPrev = page > 0;
  const hasNext = (page + 1) * PAGE_SIZE < total;

  const handleTabChange = (tab: StatusFilter) => {
    setStatusFilter(tab);
    setPage(0);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <h1 className="text-2xl font-semibold text-foreground">审批管理</h1>
        <p className="mt-1 text-sm text-secondary">
          审批高风险操作和技能执行请求
        </p>

        {/* Status filter tabs */}
        <div className="mt-4 flex gap-1">
          {STATUS_TABS.map((tab) => (
            <button
              key={tab.key}
              type="button"
              onClick={() => handleTabChange(tab.key)}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                statusFilter === tab.key
                  ? "bg-subtle text-foreground"
                  : "text-secondary hover:text-foreground hover:bg-subtle/50"
              )}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-8 pb-8">
        {query.isLoading ? (
          <div className="flex h-40 items-center justify-center">
            <Spinner />
          </div>
        ) : approvals.length === 0 ? (
          <div className="flex h-full items-center justify-center p-8">
            <div className="flex flex-col items-center gap-3 text-center">
              <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                <ShieldCheck className="h-8 w-8" strokeWidth={1.5} />
              </div>
              <h3 className="text-2xl font-semibold text-foreground">
                {statusFilter === "pending" ? "没有待审批" : "没有审批记录"}
              </h3>
              <p className="max-w-sm text-sm text-secondary">
                {statusFilter === "pending"
                  ? "所有高风险操作都已处理完毕"
                  : "切换筛选条件查看其他审批记录"}
              </p>
            </div>
          </div>
        ) : (
          <>
            <div className="space-y-3">
              {approvals.map((approval) => (
                <ApprovalCard
                  key={approval.id}
                  approval={approval}
                  rejectingId={rejectingId}
                  setRejectingId={setRejectingId}
                  rejectReason={rejectReason}
                  setRejectReason={setRejectReason}
                  onApprove={() => approveMutation.mutate(approval.id)}
                  onReject={() => rejectMutation.mutate(approval.id)}
                  approvePending={approveMutation.isPending}
                  rejectPending={rejectMutation.isPending}
                />
              ))}
            </div>

            {/* Pagination */}
            <div className="mt-6 flex items-center justify-between text-sm text-secondary">
              <span>
                共 {total} 条，第 {offset + 1}-{Math.min(offset + PAGE_SIZE, total)} 条
              </span>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!hasPrev}
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                  上一页
                </Button>
                <span className="px-2 text-xs text-tertiary">
                  {page + 1} / {Math.max(1, Math.ceil(total / PAGE_SIZE))}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!hasNext}
                  onClick={() => setPage((p) => p + 1)}
                >
                  下一页
                  <ChevronRight className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function ApprovalCard({
  approval,
  rejectingId,
  setRejectingId,
  rejectReason,
  setRejectReason,
  onApprove,
  onReject,
  approvePending,
  rejectPending,
}: {
  approval: Approval;
  rejectingId: string | null;
  setRejectingId: (id: string | null) => void;
  rejectReason: string;
  setRejectReason: (s: string) => void;
  onApprove: () => void;
  onReject: () => void;
  approvePending: boolean;
  rejectPending: boolean;
}) {
  const [showData, setShowData] = useState(false);
  const risk = approval.risk_level ? RISK_CONFIG[approval.risk_level] : null;
  const RiskIcon = risk?.icon ?? AlertTriangle;
  const hasIntentData =
    approval.intent_data && Object.keys(approval.intent_data).length > 0;

  return (
    <div
      className={cn(
        "rounded-md border p-4 shadow-sm",
        approval.status === "pending"
          ? "border-warning/30 bg-warning/5"
          : "border-border bg-elevated"
      )}
    >
      {/* Header row */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-medium text-foreground">
              {REASON_LABELS[approval.reason] ?? approval.reason}
            </span>
            <span
              className={cn(
                "rounded-full px-2 py-0.5 text-xs",
                statusBadgeClass(approval.status)
              )}
            >
              {statusLabel(approval.status)}
            </span>
            {risk && (
              <span
                className={cn(
                  "flex items-center gap-1 rounded-full px-2 py-0.5 text-xs",
                  risk.color
                )}
              >
                <RiskIcon className="h-3 w-3" />
                风险：{risk.label}
              </span>
            )}
          </div>

          {/* Operation details */}
          {(approval.tool_name || approval.resource || approval.operation) && (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {approval.tool_name && (
                <span className="flex items-center gap-1 rounded bg-subtle px-2 py-0.5 text-xs text-secondary">
                  <Wrench className="h-3 w-3" />
                  {approval.tool_name}
                </span>
              )}
              {approval.resource && (
                <span className="flex items-center gap-1 rounded bg-subtle px-2 py-0.5 text-xs text-secondary">
                  <Database className="h-3 w-3" />
                  {approval.resource}
                </span>
              )}
              {approval.operation && (
                <span className="rounded bg-subtle px-2 py-0.5 text-xs text-secondary">
                  {OPERATION_LABELS[approval.operation] ?? approval.operation}
                </span>
              )}
            </div>
          )}

          {/* Intent data (expandable JSON) */}
          {hasIntentData && (
            <div className="mt-2">
              <button
                type="button"
                onClick={() => setShowData((v) => !v)}
                className="flex items-center gap-1 text-xs text-accent hover:underline"
              >
                {showData ? (
                  <ChevronUp className="h-3 w-3" />
                ) : (
                  <ChevronDown className="h-3 w-3" />
                )}
                操作参数
              </button>
              {showData && (
                <pre className="mt-1 overflow-x-auto rounded-md bg-subtle/50 p-3 text-xs text-secondary">
                  {JSON.stringify(approval.intent_data, null, 2)}
                </pre>
              )}
            </div>
          )}

          {/* Meta info */}
          <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-tertiary">
            <span>Agent: {approval.agent_id.slice(0, 8)}</span>
            <span>·</span>
            <span>会话: {approval.session_id.slice(0, 8)}</span>
            <span>·</span>
            <span>申请时间: {relativeTime(approval.created_at)}</span>
            {approval.decided_at && (
              <>
                <span>·</span>
                <span>决定时间: {relativeTime(approval.decided_at)}</span>
              </>
            )}
          </div>
        </div>

        {/* Actions (pending only) */}
        {approval.status === "pending" && (
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={onApprove} disabled={approvePending}>
              <Check className="h-3.5 w-3.5" />
              批准
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setRejectingId(approval.id)}
              className="text-danger hover:text-danger"
            >
              <X className="h-3.5 w-3.5" />
              驳回
            </Button>
          </div>
        )}
      </div>

      {/* Reject reason input */}
      {rejectingId === approval.id && (
        <div className="mt-3 rounded-md bg-subtle/50 p-3">
          <input
            type="text"
            placeholder="驳回原因..."
            value={rejectReason}
            onChange={(e) => setRejectReason(e.target.value)}
            className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-xs text-foreground placeholder:text-tertiary focus:border-accent focus:outline-none"
          />
          <div className="mt-2 flex justify-end gap-2">
            <Button
              variant="ghost"
              size="sm"
              type="button"
              onClick={() => {
                setRejectingId(null);
                setRejectReason("");
              }}
            >
              取消
            </Button>
            <Button size="sm" type="button" onClick={onReject} disabled={rejectPending}>
              确认驳回
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
