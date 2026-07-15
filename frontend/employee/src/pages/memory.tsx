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
import { Brain, Plus, Trash2, X, Building2, Users, User, Pencil } from "lucide-react";
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
  const [searchQuery, setSearchQuery] = useState("");
  const [page, setPage] = useState(1);
  const [showCreate, setShowCreate] = useState(false);
  const [editingMemory, setEditingMemory] = useState<Memory | null>(null);

  const query = useQuery({
    queryKey: ["memory", searchQuery, scopeFilter],
    queryFn: async () => {
      const params: Record<string, unknown> = {};
      if (searchQuery) params.params = { query: { q: searchQuery } };
      else if (scopeFilter !== "all") params.params = { query: { scope: scopeFilter } };
      const { data, error } = await apiClient.GET("/memory", params);
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
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["memory"] });
      toast.show({ title: "记忆已删除", variant: "success" });
    },
  });

  const memories = query.data ?? [];
  const filteredMemories = useMemo(() => {
    if (scopeFilter === "all" || searchQuery) return memories;
    return memories.filter((m) => m.scope === scopeFilter);
  }, [memories, scopeFilter, searchQuery]);
  const PAGE_SIZE = 12;
  const total = filteredMemories.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const pagedMemories = useMemo(() => {
    const start = (safePage - 1) * PAGE_SIZE;
    return filteredMemories.slice(start, start + PAGE_SIZE);
  }, [filteredMemories, safePage]);

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">记忆中心</h1>
            <p className="mt-1 text-sm text-secondary">
              Agent 记住的信息和偏好（个人 / 部门 / 公司）
            </p>
          </div>
          <Button onClick={() => setShowCreate(true)} size="sm">
            <Plus className="h-3.5 w-3.5" />
            添加记忆
          </Button>
        </div>
      </div>

      <div className="flex items-center justify-between border-b border-border-subtle px-8 py-3">
        <div className="flex items-center gap-1 rounded-md bg-subtle p-0.5">
          {(["all", "personal", "department", "enterprise"] as ScopeFilter[]).map(
            (s) => (
              <button
                key={s}
                type="button"
                onClick={() => {
                  setScopeFilter(s);
                  setSearchQuery("");
                  setPage(1);
                }}
                className={cn(
                  "rounded-sm px-2.5 py-1 text-xs font-medium transition-colors",
                  scopeFilter === s && !searchQuery
                    ? "bg-elevated text-foreground shadow-sm"
                    : "text-secondary hover:text-foreground",
                )}
              >
                {s === "all" ? "全部" : SCOPE_LABELS[s]}
              </button>
            ),
          )}
        </div>
        <SearchInput
          value={searchQuery}
          onChange={(v) => {
            setSearchQuery(v);
            setPage(1);
          }}
          placeholder="语义搜索记忆..."
          className="w-64"
        />
      </div>

      <div className="flex-1 overflow-y-auto px-8 pb-8">
        {query.isLoading ? (
          <LoadingState />
        ) : pagedMemories.length === 0 ? (
          <EmptyState
            icon={Brain}
            title={searchQuery ? "没有匹配的记忆" : "暂无记忆"}
            description={
              searchQuery
                ? "尝试换个关键词搜索"
                : "Agent 在对话中会自动记住你的偏好和习惯，也可以手动添加"
            }
          />
        ) : (
          <div className="space-y-3">
            {pagedMemories.map((m) => {
              const ScopeIcon = SCOPE_ICONS[m.scope] ?? User;
              return (
                <div
                  key={m.id}
                  className="flex items-start gap-3 rounded-md border border-border bg-elevated p-4 shadow-sm"
                >
                  <div className="mt-0.5 shrink-0 text-secondary">
                    <Brain className="h-5 w-5" strokeWidth={1.75} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
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
                  {m.scope === "personal" && (
                    <div className="flex gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setEditingMemory(m)}
                        className="text-tertiary hover:text-foreground"
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => deleteMutation.mutate(m.id)}
                        disabled={deleteMutation.isPending}
                        className="text-tertiary hover:text-danger"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
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

      {showCreate && (
        <CreateMemoryModal
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            setShowCreate(false);
            void queryClient.invalidateQueries({ queryKey: ["memory"] });
          }}
        />
      )}

      {editingMemory && (
        <EditMemoryModal
          memory={editingMemory}
          onClose={() => setEditingMemory(null)}
          onSaved={() => {
            setEditingMemory(null);
            void queryClient.invalidateQueries({ queryKey: ["memory"] });
          }}
        />
      )}
    </div>
  );
}

function CreateMemoryModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [content, setContent] = useState("");
  const [memoryType, setMemoryType] = useState("fact");

  const createMutation = useMutation({
    mutationFn: async () => {
      await apiClient.POST("/memory", {
        body: {
          content,
          memory_type: memoryType,
          scope: "personal",
        },
      });
    },
    onSuccess: () => {
      toast.show({ title: "记忆已添加", variant: "success" });
      onCreated();
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "添加失败";
      toast.show({ title: msg, variant: "danger" });
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="w-full max-w-lg rounded-lg bg-elevated p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-foreground">添加个人记忆</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-tertiary hover:bg-subtle hover:text-foreground"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            createMutation.mutate();
          }}
          className="space-y-3"
        >
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary">
              内容
            </label>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="输入要记住的内容..."
              required
              rows={4}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-tertiary focus:border-accent focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary">
              类型
            </label>
            <select
              value={memoryType}
              onChange={(e) => setMemoryType(e.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none"
            >
              {Object.entries(TYPE_LABELS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </div>
          <p className="text-xs text-tertiary">
            员工只能创建个人记忆。如需创建部门或公司记忆，请联系管理员。
          </p>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" type="button" onClick={onClose}>
              取消
            </Button>
            <Button type="submit" disabled={createMutation.isPending}>
              {createMutation.isPending ? "添加中..." : "添加"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

function EditMemoryModal({
  memory,
  onClose,
  onSaved,
}: {
  memory: Memory;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [content, setContent] = useState(memory.content);
  const [memoryType, setMemoryType] = useState(memory.memory_type);

  const editMutation = useMutation({
    mutationFn: async () => {
      await apiClient.PATCH("/memory/{memory_id}", {
        params: { path: { memory_id: memory.id } },
        body: { content, memory_type: memoryType },
      });
    },
    onSuccess: () => {
      toast.show({ title: "记忆已更新", variant: "success" });
      onSaved();
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "更新失败";
      toast.show({ title: msg, variant: "danger" });
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="w-full max-w-lg rounded-lg bg-elevated p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-foreground">编辑记忆</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-tertiary hover:bg-subtle hover:text-foreground"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            editMutation.mutate();
          }}
          className="space-y-3"
        >
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary">
              内容
            </label>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="输入要记住的内容..."
              required
              rows={4}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-tertiary focus:border-accent focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary">
              类型
            </label>
            <select
              value={memoryType}
              onChange={(e) => setMemoryType(e.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none"
            >
              {Object.entries(TYPE_LABELS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" type="button" onClick={onClose}>
              取消
            </Button>
            <Button type="submit" disabled={editMutation.isPending}>
              {editMutation.isPending ? "保存中..." : "保存"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
