import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import { Badge, Button, Spinner, cn, toast } from "@eaos/shared";
import { FileText, Check, X } from "lucide-react";
import { relativeTime } from "../lib/relative-time";

interface Contribution {
  id: string;
  submitter_id: string;
  source_type: string;
  source_uri: string | null;
  title: string;
  content: string;
  status: "pending" | "approved" | "rejected";
  reviewer_id: string | null;
  review_comment: string | null;
  submitted_at: string | null;
  reviewed_at: string | null;
}

const STATUS_VARIANT: Record<Contribution["status"], "warning" | "success" | "danger"> = {
  pending: "warning",
  approved: "success",
  rejected: "danger",
};

const STATUS_LABEL: Record<Contribution["status"], string> = {
  pending: "待审核",
  approved: "已通过",
  rejected: "已驳回",
};

type StatusFilter = "all" | Contribution["status"];

const FILTER_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "pending", label: "待审核" },
  { value: "approved", label: "已通过" },
  { value: "rejected", label: "已驳回" },
];

export default function ContributionsPage() {
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("pending");
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  const query = useQuery({
    queryKey: ["admin", "contributions", statusFilter],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/contributions", {
        params: { query: statusFilter === "all" ? {} : { status: statusFilter } },
      });
      if (error || !data) return [] as Contribution[];
      return data as unknown as Contribution[];
    },
    refetchInterval: 30_000,
  });

  const approveMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiClient.POST("/admin/contributions/{contribution_id}/review", {
        params: { path: { contribution_id: id } },
        body: { decision: "approved" },
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "contributions"] });
      toast.show({ title: "已通过，已加入知识库", variant: "success" });
    },
    onError: (err: Error) => {
      toast.show({ title: err.message || "操作失败", variant: "danger" });
    },
  });

  const rejectMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiClient.POST("/admin/contributions/{contribution_id}/review", {
        params: { path: { contribution_id: id } },
        body: { decision: "rejected", reason: rejectReason || null },
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "contributions"] });
      setRejectingId(null);
      setRejectReason("");
      toast.show({ title: "已驳回", variant: "success" });
    },
    onError: (err: Error) => {
      toast.show({ title: err.message || "操作失败", variant: "danger" });
    },
  });

  const contributions = query.data ?? [];

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">贡献审核</h1>
            <p className="mt-1 text-sm text-secondary">
              审核员工提交的知识贡献，通过后自动入库
            </p>
          </div>
          {/* Status filter */}
          <div className="flex items-center gap-1 rounded-md border border-border bg-elevated p-1">
            {FILTER_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => setStatusFilter(opt.value)}
                className={cn(
                  "rounded px-2.5 py-1 text-xs font-medium transition-colors",
                  statusFilter === opt.value
                    ? "bg-accent text-white"
                    : "text-secondary hover:text-foreground"
                )}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-8 pb-8">
        {query.isLoading ? (
          <div className="flex h-40 items-center justify-center">
            <Spinner />
          </div>
        ) : contributions.length === 0 ? (
          <div className="flex h-full items-center justify-center p-8">
            <div className="flex flex-col items-center gap-3 text-center">
              <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                <FileText className="h-8 w-8" strokeWidth={1.5} />
              </div>
              <h3 className="text-2xl font-semibold text-foreground">暂无贡献</h3>
              <p className="max-w-sm text-sm text-secondary">
                {statusFilter === "pending"
                  ? "目前没有待审核的贡献"
                  : "切换筛选条件查看更多贡献"}
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            {contributions.map((c) => (
              <div
                key={c.id}
                className={cn(
                  "rounded-md border p-4 shadow-sm",
                  c.status === "pending"
                    ? "border-warning/30 bg-warning/5"
                    : "border-border bg-elevated"
                )}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-foreground">{c.title}</span>
                      <Badge variant={STATUS_VARIANT[c.status]}>
                        {STATUS_LABEL[c.status]}
                      </Badge>
                      <Badge variant="outline">{c.source_type}</Badge>
                    </div>
                    <p className="mt-2 line-clamp-3 text-sm text-secondary">
                      {c.content}
                    </p>
                    {c.review_comment && (
                      <div className="mt-2 rounded-md bg-subtle px-3 py-2 text-xs text-secondary">
                        <span className="font-medium text-foreground">审核反馈：</span>
                        {c.review_comment}
                      </div>
                    )}
                    <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-tertiary">
                      <span>提交人：{c.submitter_id.slice(0, 8)}</span>
                      <span>·</span>
                      {c.submitted_at && (
                        <span>提交于 {relativeTime(c.submitted_at)}</span>
                      )}
                      {c.reviewed_at && (
                        <>
                          <span>·</span>
                          <span>审核于 {relativeTime(c.reviewed_at)}</span>
                        </>
                      )}
                      {c.source_uri && (
                        <>
                          <span>·</span>
                          <span className="max-w-[200px] truncate">来源：{c.source_uri}</span>
                        </>
                      )}
                    </div>
                  </div>

                  {c.status === "pending" && (
                    <div className="flex items-center gap-2">
                      <Button
                        size="sm"
                        onClick={() => approveMutation.mutate(c.id)}
                        disabled={approveMutation.isPending}
                      >
                        <Check className="h-3.5 w-3.5" />
                        通过
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setRejectingId(c.id)}
                        className="text-danger hover:text-danger"
                      >
                        <X className="h-3.5 w-3.5" />
                        驳回
                      </Button>
                    </div>
                  )}
                </div>

                {/* Reject reason input */}
                {rejectingId === c.id && (
                  <div className="mt-3 rounded-md bg-subtle/50 p-3">
                    <textarea
                      placeholder="驳回原因（可选）..."
                      value={rejectReason}
                      onChange={(e) => setRejectReason(e.target.value)}
                      rows={2}
                      className="w-full resize-none rounded-md border border-border bg-background px-3 py-1.5 text-xs text-foreground placeholder:text-tertiary focus:border-accent focus:outline-none"
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
                      <Button
                        size="sm"
                        type="button"
                        onClick={() => rejectMutation.mutate(c.id)}
                        disabled={rejectMutation.isPending}
                      >
                        确认驳回
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
