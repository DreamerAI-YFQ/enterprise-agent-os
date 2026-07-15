import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import {
  Button,
  cn,
  toast,
  SearchInput,
  Pagination,
  LoadingState,
  EmptyState,
} from "@eaos/shared";
import {
  Brain,
  Trash2,
  ArrowUpCircle,
  Building2,
  Users,
  User,
  X,
  Check,
} from "lucide-react";
import { relativeTime } from "../lib/relative-time";

interface Memory {
  id: string;
  scope: string;
  owner_id: string | null;
  memory_type: string;
  content: string;
  confidence: number;
  source: string;
  created_at: string;
  last_accessed: string | null;
  access_count: number;
}

interface Department {
  id: string;
  name: string;
}

const TYPE_LABELS: Record<string, string> = {
  fact: "事实",
  preference: "偏好",
  instruction: "指令",
  context: "上下文",
};

const SCOPE_LABELS: Record<string, string> = {
  personal: "个人",
  department: "部门",
  enterprise: "公司",
};

const SCOPE_ICONS: Record<string, typeof User> = {
  personal: User,
  department: Users,
  enterprise: Building2,
};

const SCOPE_COLORS: Record<string, string> = {
  personal: "bg-accent-subtle text-accent",
  department: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300",
  enterprise: "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300",
};

type ScopeFilter = "all" | "personal" | "department" | "enterprise";

export default function MemoryPage() {
  const queryClient = useQueryClient();
  const [scopeFilter, setScopeFilter] = useState<ScopeFilter>("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [promotingId, setPromotingId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [showBatchPromote, setShowBatchPromote] = useState(false);

  const query = useQuery({
    queryKey: ["admin", "memory", scopeFilter, search],
    queryFn: async () => {
      const queryParams: Record<string, string> = {};
      if (search) {
        queryParams.q = search;
      } else if (scopeFilter !== "all") {
        queryParams.scope = scopeFilter;
      }
      const { data, error } = await apiClient.GET("/admin/memory", {
        params: { query: queryParams },
      });
      if (error || !data) return [] as Memory[];
      return data as unknown as Memory[];
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiClient.DELETE("/memory/{memory_id}", {
        params: { path: { memory_id: id } },
      });
    },
    onMutate: async (id: string) => {
      await queryClient.cancelQueries({ queryKey: ["admin", "memory"] });
      const previous = queryClient.getQueryData<Memory[]>(["admin", "memory", scopeFilter, search]);
      if (previous) {
        queryClient.setQueryData<Memory[]>(
          ["admin", "memory", scopeFilter, search],
          previous.filter((m) => m.id !== id),
        );
      }
      return { previous };
    },
    onSuccess: () => {
      toast.show({ title: "记忆已删除", variant: "success" });
    },
    onError: (err: unknown, _id, context) => {
      if (context?.previous) {
        queryClient.setQueryData(["admin", "memory", scopeFilter, search], context.previous);
      }
      const msg = err instanceof Error ? err.message : "删除失败";
      toast.show({ title: msg, variant: "danger" });
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "memory"] });
    },
  });

  const batchDeleteMutation = useMutation({
    mutationFn: async (ids: string[]) => {
      await apiClient.POST("/admin/memory/batch-delete", {
        body: { memory_ids: ids },
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "memory"] });
      setSelectedIds(new Set());
      toast.show({ title: "批量删除完成", variant: "success" });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "批量删除失败";
      toast.show({ title: msg, variant: "danger" });
    },
  });

  const memories = query.data ?? [];
  const filteredMemories = useMemo(() => {
    if (search) return memories;
    if (scopeFilter === "all") return memories;
    return memories.filter((m) => m.scope === scopeFilter);
  }, [memories, scopeFilter, search]);
  const PAGE_SIZE = 12;
  const total = filteredMemories.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const pagedMemories = useMemo(() => {
    const start = (safePage - 1) * PAGE_SIZE;
    return filteredMemories.slice(start, start + PAGE_SIZE);
  }, [filteredMemories, safePage]);

  const allOnPageSelected =
    pagedMemories.length > 0 && pagedMemories.every((m) => selectedIds.has(m.id));

  const toggleSelectAll = () => {
    const next = new Set(selectedIds);
    if (allOnPageSelected) {
      pagedMemories.forEach((m) => next.delete(m.id));
    } else {
      pagedMemories.forEach((m) => next.add(m.id));
    }
    setSelectedIds(next);
  };

  const toggleSelect = (id: string) => {
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelectedIds(next);
  };

  const handleBatchDelete = () => {
    if (selectedIds.size === 0) return;
    batchDeleteMutation.mutate(Array.from(selectedIds));
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <h1 className="text-2xl font-semibold text-foreground">记忆管理</h1>
        <p className="mt-1 text-sm text-secondary">
          管理所有租户记忆，可晋升个人记忆到部门或公司级别
        </p>

        {/* Search + Scope filter */}
        <div className="mt-4 flex items-center gap-3">
          <SearchInput
            value={search}
            onChange={(v) => {
              setSearch(v);
              setPage(1);
            }}
            placeholder="语义搜索记忆..."
            className="max-w-xs"
          />
          <div className="flex gap-1 rounded-md bg-subtle p-1">
            {(
              ["all", "personal", "department", "enterprise"] as ScopeFilter[]
            ).map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => {
                  setScopeFilter(s);
                  setSearch("");
                  setPage(1);
                }}
                disabled={!!search}
                className={cn(
                  "rounded px-3 py-1.5 text-xs font-medium transition-colors disabled:opacity-40",
                  scopeFilter === s && !search
                    ? "bg-background text-foreground shadow-sm"
                    : "text-secondary hover:text-foreground",
                )}
              >
                {s === "all" ? "全部" : SCOPE_LABELS[s]}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-8 pb-8">
        {query.isLoading ? (
          <LoadingState />
        ) : pagedMemories.length === 0 ? (
          <EmptyState
            icon={Brain}
            title="暂无记忆"
            description={search || scopeFilter !== "all" ? "未找到匹配的记忆" : undefined}
          />
        ) : (
          <>
            {/* Select all bar */}
            <div className="mb-2 flex items-center gap-2 px-1">
              <button
                type="button"
                onClick={toggleSelectAll}
                className="flex items-center gap-1.5 text-xs text-secondary hover:text-foreground"
              >
                <span
                  className={cn(
                    "flex h-4 w-4 items-center justify-center rounded border",
                    allOnPageSelected
                      ? "border-accent bg-accent text-white"
                      : "border-border",
                  )}
                >
                  {allOnPageSelected && <Check className="h-3 w-3" />}
                </span>
                {allOnPageSelected ? "取消全选" : "全选本页"}
              </button>
              {selectedIds.size > 0 && (
                <span className="text-xs text-tertiary">
                  已选 {selectedIds.size} 项
                </span>
              )}
            </div>

            <div className="space-y-3">
              {pagedMemories.map((m) => {
                const ScopeIcon = SCOPE_ICONS[m.scope] ?? User;
                const isSelected = selectedIds.has(m.id);
                return (
                  <div
                    key={m.id}
                    className={cn(
                      "rounded-md border bg-elevated p-4 shadow-sm transition-colors",
                      isSelected ? "border-accent" : "border-border",
                    )}
                  >
                    <div className="flex items-start gap-3">
                      <button
                        type="button"
                        onClick={() => toggleSelect(m.id)}
                        className="mt-1 shrink-0"
                      >
                        <span
                          className={cn(
                            "flex h-4 w-4 items-center justify-center rounded border",
                            isSelected
                              ? "border-accent bg-accent text-white"
                              : "border-border hover:border-accent",
                          )}
                        >
                          {isSelected && <Check className="h-3 w-3" />}
                        </span>
                      </button>
                      <div className="mt-0.5 shrink-0 text-secondary">
                        <Brain className="h-5 w-5" strokeWidth={1.75} />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span
                            className={cn(
                              "flex items-center gap-1 rounded-full px-1.5 py-0.5 text-xs",
                              SCOPE_COLORS[m.scope] ?? "bg-subtle text-tertiary",
                            )}
                          >
                            <ScopeIcon className="h-3 w-3" />
                            {SCOPE_LABELS[m.scope] ?? m.scope}
                          </span>
                          <span className="rounded-full bg-subtle px-1.5 py-0.5 text-xs text-tertiary">
                            {TYPE_LABELS[m.memory_type] ?? m.memory_type}
                          </span>
                          <span className="text-xs text-tertiary">
                            置信度 {Math.round(m.confidence * 100)}%
                          </span>
                          <span className="text-xs text-tertiary">
                            来源: {m.source}
                          </span>
                          <span className="text-xs text-tertiary">
                            · {relativeTime(m.created_at)}
                          </span>
                        </div>
                        <p className="mt-1 text-sm text-foreground">{m.content}</p>
                        {m.access_count > 0 && (
                          <p className="mt-1 text-xs text-tertiary">
                            被引用 {m.access_count} 次
                          </p>
                        )}
                      </div>
                      <div className="flex flex-col gap-1">
                        {m.scope !== "enterprise" && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setPromotingId(m.id)}
                          >
                            <ArrowUpCircle className="h-3.5 w-3.5" />
                            晋升
                          </Button>
                        )}
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => deleteMutation.mutate(m.id)}
                          disabled={deleteMutation.isPending}
                          className="text-danger hover:text-danger"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                          删除
                        </Button>
                      </div>
                    </div>

                    {promotingId === m.id && (
                      <PromotePanel
                        memory={m}
                        onCancel={() => setPromotingId(null)}
                        onDone={() => {
                          setPromotingId(null);
                          void queryClient.invalidateQueries({
                            queryKey: ["admin", "memory"],
                          });
                        }}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          </>
        )}
        {total > PAGE_SIZE && (
          <Pagination
            page={safePage}
            pageSize={PAGE_SIZE}
            total={total}
            onPageChange={setPage}
          />
        )}
      </div>

      {/* Batch action bar */}
      {selectedIds.size > 0 && (
        <div className="border-t border-border-subtle bg-elevated px-8 py-3 shadow-lg">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span className="text-sm font-medium text-foreground">
                已选 {selectedIds.size} 项
              </span>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setSelectedIds(new Set())}
              >
                清除选择
              </Button>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setShowBatchPromote(true)}
              >
                <ArrowUpCircle className="h-3.5 w-3.5" />
                批量晋升
              </Button>
              <Button
                variant="danger"
                size="sm"
                onClick={handleBatchDelete}
                disabled={batchDeleteMutation.isPending}
              >
                <Trash2 className="h-3.5 w-3.5" />
                批量删除
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Batch promote modal */}
      {showBatchPromote && (
        <BatchPromoteModal
          selectedIds={Array.from(selectedIds)}
          onClose={() => setShowBatchPromote(false)}
          onDone={() => {
            setShowBatchPromote(false);
            setSelectedIds(new Set());
            void queryClient.invalidateQueries({
              queryKey: ["admin", "memory"],
            });
          }}
        />
      )}
    </div>
  );
}

function PromotePanel({
  memory,
  onCancel,
  onDone,
}: {
  memory: Memory;
  onCancel: () => void;
  onDone: () => void;
}) {
  const [newScope, setNewScope] = useState(
    memory.scope === "personal" ? "department" : "enterprise",
  );
  const [departmentId, setDepartmentId] = useState("");

  const { data: departments } = useQuery({
    queryKey: ["departments"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/departments", {});
      if (error || !data) return [] as Department[];
      return data as unknown as Department[];
    },
  });

  const promoteMutation = useMutation({
    mutationFn: async () => {
      await apiClient.POST("/admin/memory/{memory_id}/promote", {
        params: { path: { memory_id: memory.id } },
        body: {
          new_scope: newScope,
          new_owner_id: newScope === "department" ? departmentId : null,
        },
      });
    },
    onSuccess: () => {
      toast.show({ title: "记忆已晋升", variant: "success" });
      onDone();
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "晋升失败";
      toast.show({ title: msg, variant: "danger" });
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (newScope === "department" && !departmentId) {
      toast.show({ title: "请选择部门", variant: "danger" });
      return;
    }
    promoteMutation.mutate();
  };

  return (
    <div className="mt-3 rounded-md bg-subtle/50 p-3">
      <form onSubmit={handleSubmit} className="space-y-2">
        <div className="flex items-center gap-2">
          <label className="text-xs font-medium text-secondary">晋升到:</label>
          <select
            value={newScope}
            onChange={(e) => setNewScope(e.target.value)}
            className="rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground focus:outline-none"
          >
            {memory.scope === "personal" && (
              <option value="department">部门</option>
            )}
            <option value="enterprise">公司</option>
          </select>
        </div>
        {newScope === "department" && (
          <select
            value={departmentId}
            onChange={(e) => setDepartmentId(e.target.value)}
            required
            className="w-full rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground focus:outline-none"
          >
            <option value="">选择部门...</option>
            {departments?.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" size="sm" type="button" onClick={onCancel}>
            取消
          </Button>
          <Button size="sm" type="submit" disabled={promoteMutation.isPending}>
            {promoteMutation.isPending ? "晋升中..." : "确认晋升"}
          </Button>
        </div>
      </form>
    </div>
  );
}

function BatchPromoteModal({
  selectedIds,
  onClose,
  onDone,
}: {
  selectedIds: string[];
  onClose: () => void;
  onDone: () => void;
}) {
  const [newScope, setNewScope] = useState("enterprise");
  const [departmentId, setDepartmentId] = useState("");

  const { data: departments } = useQuery({
    queryKey: ["departments"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/departments", {});
      if (error || !data) return [] as Department[];
      return data as unknown as Department[];
    },
  });

  const batchPromoteMutation = useMutation({
    mutationFn: async () => {
      await apiClient.POST("/admin/memory/batch-promote", {
        body: {
          memory_ids: selectedIds,
          new_scope: newScope,
          new_owner_id: newScope === "department" ? departmentId : null,
        },
      });
    },
    onSuccess: () => {
      toast.show({ title: `已批量晋升 ${selectedIds.length} 条记忆`, variant: "success" });
      onDone();
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "批量晋升失败";
      toast.show({ title: msg, variant: "danger" });
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (newScope === "department" && !departmentId) {
      toast.show({ title: "请选择部门", variant: "danger" });
      return;
    }
    batchPromoteMutation.mutate();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-lg bg-elevated p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-foreground">
            批量晋升记忆
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-tertiary hover:bg-subtle hover:text-foreground"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <p className="mb-3 text-sm text-secondary">
          将选中的 {selectedIds.length} 条记忆晋升到更高级别
        </p>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary">
              晋升到
            </label>
            <select
              value={newScope}
              onChange={(e) => setNewScope(e.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none"
            >
              <option value="department">部门</option>
              <option value="enterprise">公司</option>
            </select>
          </div>
          {newScope === "department" && (
            <div>
              <label className="mb-1 block text-xs font-medium text-secondary">
                目标部门
              </label>
              <select
                value={departmentId}
                onChange={(e) => setDepartmentId(e.target.value)}
                required
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none"
              >
                <option value="">选择部门...</option>
                {departments?.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            </div>
          )}
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" type="button" onClick={onClose}>
              取消
            </Button>
            <Button type="submit" disabled={batchPromoteMutation.isPending}>
              {batchPromoteMutation.isPending
                ? "晋升中..."
                : `确认晋升 ${selectedIds.length} 项`}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
