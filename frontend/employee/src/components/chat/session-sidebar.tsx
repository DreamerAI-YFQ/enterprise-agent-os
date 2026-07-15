import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Trash2,
  MessageSquare,
  Pencil,
  Download,
  Sparkles,
  Check,
  X,
} from "lucide-react";
import { Button, EmptyState, Spinner, cn, toast, SearchInput } from "@eaos/shared";
import { apiClient } from "@eaos/shared/api";
import { relativeTime } from "../../lib/relative-time";
import type { SessionSummary } from "../../hooks/use-chat";

interface SessionSidebarProps {
  sessions: SessionSummary[];
  isLoading: boolean;
  currentSessionId: string | null;
  onSelect: (sessionId: string) => void;
  onNew: () => void;
  onDelete: (sessionId: string) => void;
}

/**
 * F1-T9 + P1-T4 — Session list sidebar with search, rename, export, auto-title.
 */
export function SessionSidebar({
  sessions,
  isLoading,
  currentSessionId,
  onSelect,
  onNew,
  onDelete,
}: SessionSidebarProps) {
  const [search, setSearch] = useState("");

  const filtered = search.trim()
    ? sessions.filter((s) =>
        (s.title ?? "新对话").toLowerCase().includes(search.trim().toLowerCase()),
      )
    : sessions;

  return (
    <aside className="flex w-[260px] shrink-0 flex-col border-r border-border bg-elevated">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3">
        <span className="text-sm font-semibold text-foreground">对话</span>
        <Button size="icon-sm" variant="ghost" onClick={onNew} aria-label="新建对话">
          <Plus className="h-4 w-4" />
        </Button>
      </div>

      {/* Search */}
      <div className="px-3 pb-2">
        <SearchInput
          value={search}
          onChange={setSearch}
          placeholder="搜索对话..."
          className="w-full"
        />
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto px-2 pb-2">
        {isLoading ? (
          <div className="flex items-center justify-center py-8">
            <Spinner size="sm" />
          </div>
        ) : filtered.length === 0 ? (
          <EmptyState
            icon={MessageSquare}
            title={search ? "未找到对话" : "暂无对话"}
            description={search ? "换个关键词试试" : "点击上方 + 开始新对话"}
            compact
          />
        ) : (
          <ul className="space-y-0.5">
            {filtered.map((session) => (
              <SessionCard
                key={session.id}
                session={session}
                isActive={session.id === currentSessionId}
                onSelect={onSelect}
                onDelete={onDelete}
              />
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}

function SessionCard({
  session,
  isActive,
  onSelect,
  onDelete,
}: {
  session: SessionSummary;
  isActive: boolean;
  onSelect: (sessionId: string) => void;
  onDelete: (sessionId: string) => void;
}) {
  const queryClient = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState(session.title ?? "");

  const title = session.title?.trim() || "新对话";

  const renameMutation = useMutation({
    mutationFn: async (newTitle: string) => {
      await apiClient.PATCH("/sessions/{session_id}", {
        params: { path: { session_id: session.id } },
        body: { title: newTitle },
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["sessions"] });
      setEditing(false);
      toast.show({ title: "已重命名", variant: "success" });
    },
    onError: () => {
      toast.show({ title: "重命名失败", variant: "danger" });
    },
  });

  const autoTitleMutation = useMutation({
    mutationFn: async () => {
      const { data, error } = await apiClient.POST(
        "/sessions/{session_id}/title/auto",
        { params: { path: { session_id: session.id } } },
      );
      if (error) throw new Error("auto-title failed");
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["sessions"] });
      toast.show({ title: "已自动生成标题", variant: "success" });
    },
    onError: () => {
      toast.show({ title: "自动标题失败", variant: "danger" });
    },
  });

  const handleExport = async () => {
    try {
      const { data, error } = await apiClient.GET("/sessions/{session_id}/export", {
        params: { path: { session_id: session.id } },
        parseAs: "text",
      });
      if (error || !data) throw new Error("export failed");
      const blob = new Blob([data], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${title.replace(/[<>:"/\\|?*]/g, "_")}.md`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      toast.show({ title: "已导出对话", variant: "success" });
    } catch {
      toast.show({ title: "导出失败", variant: "danger" });
    }
  };

  const handleSaveRename = () => {
    const trimmed = editTitle.trim();
    if (!trimmed || trimmed === title) {
      setEditing(false);
      return;
    }
    renameMutation.mutate(trimmed);
  };

  return (
    <li>
      <div
        className={cn(
          "group relative cursor-pointer rounded-md px-3 py-2 transition-colors",
          isActive ? "bg-accent-subtle" : "hover:bg-subtle",
        )}
        onClick={() => !editing && onSelect(session.id)}
      >
        {editing ? (
          <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
            <input
              type="text"
              value={editTitle}
              onChange={(e) => setEditTitle(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSaveRename();
                if (e.key === "Escape") {
                  setEditing(false);
                  setEditTitle(title);
                }
              }}
              autoFocus
              className="w-full rounded border border-accent bg-background px-1.5 py-0.5 text-sm text-foreground focus:outline-none"
            />
            <button
              type="button"
              onClick={handleSaveRename}
              className="rounded p-0.5 text-accent hover:bg-accent-subtle"
            >
              <Check className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={() => {
                setEditing(false);
                setEditTitle(title);
              }}
              className="rounded p-0.5 text-tertiary hover:bg-subtle"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ) : (
          <>
            <div className="flex items-center gap-2">
              <MessageSquare
                className={cn(
                  "h-3.5 w-3.5 shrink-0",
                  isActive ? "text-accent" : "text-tertiary",
                )}
                strokeWidth={1.75}
              />
              <span
                className={cn(
                  "flex-1 truncate text-sm",
                  isActive ? "font-medium text-accent" : "text-foreground",
                )}
              >
                {title}
              </span>
            </div>
            <div className="mt-0.5 pl-5 text-[10px] text-tertiary">
              {relativeTime(session.last_active_at)}
            </div>

            {/* Action buttons — appear on hover */}
            {confirmDelete ? (
              <div className="absolute right-1.5 top-1.5 flex items-center gap-1 rounded bg-danger-subtle px-1.5 py-0.5">
                <span className="text-[10px] font-medium text-danger">删除?</span>
                <button
                  type="button"
                  className="rounded p-0.5 text-danger hover:bg-danger hover:text-white"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDelete(session.id);
                    setConfirmDelete(false);
                  }}
                >
                  <Check className="h-3 w-3" />
                </button>
                <button
                  type="button"
                  className="rounded p-0.5 text-danger hover:bg-danger hover:text-white"
                  onClick={(e) => {
                    e.stopPropagation();
                    setConfirmDelete(false);
                  }}
                >
                  <X className="h-3 w-3" />
                </button>
              </div>
            ) : (
              <div className="absolute right-1.5 top-1.5 hidden items-center gap-0.5 rounded bg-elevated/80 backdrop-blur-sm group-hover:flex">
                <button
                  type="button"
                  className="rounded p-1 text-tertiary hover:bg-subtle hover:text-foreground"
                  onClick={(e) => {
                    e.stopPropagation();
                    setEditTitle(title);
                    setEditing(true);
                  }}
                  aria-label="重命名"
                  title="重命名"
                >
                  <Pencil className="h-3 w-3" />
                </button>
                <button
                  type="button"
                  className="rounded p-1 text-tertiary hover:bg-subtle hover:text-foreground"
                  onClick={(e) => {
                    e.stopPropagation();
                    autoTitleMutation.mutate();
                  }}
                  disabled={autoTitleMutation.isPending}
                  aria-label="自动标题"
                  title="自动生成标题"
                >
                  <Sparkles className="h-3 w-3" />
                </button>
                <button
                  type="button"
                  className="rounded p-1 text-tertiary hover:bg-subtle hover:text-foreground"
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleExport();
                  }}
                  aria-label="导出"
                  title="导出为 Markdown"
                >
                  <Download className="h-3 w-3" />
                </button>
                <button
                  type="button"
                  className="rounded p-1 text-tertiary hover:bg-danger-subtle hover:text-danger"
                  onClick={(e) => {
                    e.stopPropagation();
                    setConfirmDelete(true);
                  }}
                  aria-label="删除对话"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </li>
  );
}
