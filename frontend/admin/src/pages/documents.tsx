import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import { Button, Spinner, cn, toast } from "@eaos/shared";
import { ChevronDown, ChevronRight, FileText, Plus, X, Trash2, Building2, Users, User } from "lucide-react";
import { relativeTime } from "../lib/relative-time";

interface Doc {
  id: string;
  source_type: string;
  source_uri: string;
  title: string;
  content_hash: string | null;
  version: number;
  status: string;
  scope: string;
  owner_id: string | null;
  created_at: string;
}

interface Chunk {
  id: string;
  document_id: string;
  chunk_index: number;
  content: string;
  token_count: number;
  metadata: Record<string, unknown>;
  created_at: string;
}

interface Department {
  id: string;
  name: string;
}

const SOURCE_LABELS: Record<string, string> = {
  pdf: "PDF",
  word: "Word",
  confluence: "Confluence",
  email: "邮件",
  web: "网页",
  manual: "手动",
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

export default function DocumentsPage() {
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [expandedDocId, setExpandedDocId] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["admin", "documents"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/admin/knowledge/documents",
        {},
      );
      if (error || !data) return [] as Doc[];
      return data as unknown as Doc[];
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiClient.DELETE("/admin/knowledge/documents/{document_id}", {
        params: { path: { document_id: id } },
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["admin", "documents"],
      });
      toast.show({ title: "文档已删除", variant: "success" });
    },
  });

  const docs = query.data ?? [];

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">
              文档管理
            </h1>
            <p className="mt-1 text-sm text-secondary">
              管理知识库文档和分块索引
            </p>
          </div>
          <Button onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4" />
            导入文档
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-8 pb-8">
        {query.isLoading ? (
          <div className="flex h-40 items-center justify-center">
            <Spinner />
          </div>
        ) : docs.length === 0 ? (
          <div className="flex h-full items-center justify-center p-8">
            <div className="flex flex-col items-center gap-3 text-center">
              <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                <FileText className="h-8 w-8" strokeWidth={1.5} />
              </div>
              <h3 className="text-2xl font-semibold text-foreground">
                暂无文档
              </h3>
              <p className="max-w-sm text-sm text-secondary">
                导入文档到知识库，Agent 将自动分块和索引
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            {docs.map((doc) => {
              const isExpanded = expandedDocId === doc.id;
              const ScopeIcon = SCOPE_ICONS[doc.scope] ?? Building2;
              return (
                <div
                  key={doc.id}
                  className="rounded-md border border-border bg-elevated p-4 shadow-sm"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() =>
                            setExpandedDocId(isExpanded ? null : doc.id)
                          }
                          className="rounded p-0.5 text-tertiary hover:bg-subtle hover:text-foreground"
                          aria-label={isExpanded ? "收起分块" : "展开分块"}
                        >
                          {isExpanded ? (
                            <ChevronDown className="h-4 w-4" />
                          ) : (
                            <ChevronRight className="h-4 w-4" />
                          )}
                        </button>
                        <span className="font-medium text-foreground">
                          {doc.title}
                        </span>
                        <span
                          className={cn(
                            "rounded-full px-2 py-0.5 text-xs",
                            doc.status === "indexed"
                              ? "bg-success/10 text-success"
                              : "bg-warning/10 text-warning",
                          )}
                        >
                          {doc.status === "indexed" ? "已索引" : doc.status}
                        </span>
                        <span
                          className={cn(
                            "flex items-center gap-1 rounded-full px-2 py-0.5 text-xs",
                            SCOPE_COLORS[doc.scope] ?? "bg-subtle text-tertiary",
                          )}
                        >
                          <ScopeIcon className="h-3 w-3" />
                          {SCOPE_LABELS[doc.scope] ?? doc.scope}
                        </span>
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-3 pl-6 text-xs text-tertiary">
                        <span>
                          类型:{" "}
                          {SOURCE_LABELS[doc.source_type] ?? doc.source_type}
                        </span>
                        <span>·</span>
                        <span>v{doc.version}</span>
                        <span>·</span>
                        <span>{relativeTime(doc.created_at)}</span>
                      </div>
                      <p className="mt-1 truncate pl-6 text-xs text-tertiary">
                        {doc.source_uri}
                      </p>
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => deleteMutation.mutate(doc.id)}
                      disabled={deleteMutation.isPending}
                      className="text-danger hover:text-danger"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                  {isExpanded && <ChunksList documentId={doc.id} />}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {showCreate && (
        <CreateDocModal
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            setShowCreate(false);
            void queryClient.invalidateQueries({
              queryKey: ["admin", "documents"],
            });
          }}
        />
      )}
    </div>
  );
}

function CreateDocModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [title, setTitle] = useState("");
  const [sourceType, setSourceType] = useState("manual");
  const [sourceUri, setSourceUri] = useState("");
  const [content, setContent] = useState("");
  const [scope, setScope] = useState("enterprise");
  const [departmentId, setDepartmentId] = useState("");

  const { data: departments } = useQuery({
    queryKey: ["departments"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/departments", {});
      if (error || !data) return [] as Department[];
      return data as unknown as Department[];
    },
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      await apiClient.POST("/admin/knowledge/documents", {
        body: {
          title,
          source_type: sourceType,
          source_uri: sourceUri || `manual://${title}`,
          content,
          metadata: {},
          version: 1,
          scope,
          owner_id: scope === "department" ? departmentId : null,
        },
      });
    },
    onSuccess: () => {
      toast.show({ title: "文档已导入", variant: "success" });
      onCreated();
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "导入失败";
      toast.show({ title: msg, variant: "danger" });
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (scope === "department" && !departmentId) {
      toast.show({ title: "请选择部门", variant: "danger" });
      return;
    }
    createMutation.mutate();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-lg bg-elevated p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-foreground">导入文档</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-tertiary hover:bg-subtle hover:text-foreground"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="space-y-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary">
              标题
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-accent focus:outline-none"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-secondary">
                来源类型
              </label>
              <select
                value={sourceType}
                onChange={(e) => setSourceType(e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none"
              >
                {Object.entries(SOURCE_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-secondary">
                来源 URI
              </label>
              <input
                type="text"
                value={sourceUri}
                onChange={(e) => setSourceUri(e.target.value)}
                placeholder="可选，自动生成"
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-tertiary focus:border-accent focus:outline-none"
              />
            </div>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary">
              可见范围（Scope）
            </label>
            <div className="flex gap-2">
              {(["personal", "department", "enterprise"] as const).map((s) => {
                const Icon = SCOPE_ICONS[s];
                return (
                  <button
                    key={s}
                    type="button"
                    onClick={() => setScope(s)}
                    className={cn(
                      "flex flex-1 items-center justify-center gap-1.5 rounded-md border px-3 py-2 text-sm font-medium transition-colors",
                      scope === s
                        ? "border-accent bg-accent-subtle text-accent"
                        : "border-border text-secondary hover:bg-subtle",
                    )}
                  >
                    <Icon className="h-3.5 w-3.5" />
                    {SCOPE_LABELS[s]}
                  </button>
                );
              })}
            </div>
          </div>
          {scope === "department" && (
            <div>
              <label className="mb-1 block text-xs font-medium text-secondary">
                所属部门
              </label>
              <select
                value={departmentId}
                onChange={(e) => setDepartmentId(e.target.value)}
                required
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none"
              >
                <option value="">请选择部门...</option>
                {departments?.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
            </div>
          )}
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary">
              内容
            </label>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              required
              rows={6}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-accent focus:outline-none"
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" type="button" onClick={onClose}>
              取消
            </Button>
            <Button type="submit" disabled={createMutation.isPending}>
              {createMutation.isPending ? "导入中..." : "导入"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

function ChunksList({ documentId }: { documentId: string }) {
  const query = useQuery({
    queryKey: ["admin", "documents", documentId, "chunks"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/admin/knowledge/documents/{document_id}/chunks",
        {
          params: { path: { document_id: documentId } },
        },
      );
      if (error || !data) return [] as Chunk[];
      return data as unknown as Chunk[];
    },
  });

  if (query.isLoading) {
    return (
      <div className="mt-3 flex items-center gap-2 pl-6 text-sm text-tertiary">
        <Spinner />
        <span>加载分块中...</span>
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="mt-3 pl-6 text-sm text-danger">
        加载分块失败
      </div>
    );
  }

  const chunks = query.data ?? [];
  if (chunks.length === 0) {
    return (
      <div className="mt-3 pl-6 text-sm text-tertiary">
        暂无分块（文档可能尚未索引）
      </div>
    );
  }

  const totalTokens = chunks.reduce((sum, c) => sum + c.token_count, 0);

  return (
    <div className="mt-3 space-y-2 border-l border-border-subtle pl-6">
      <div className="flex items-center gap-3 text-xs text-tertiary">
        <span>共 {chunks.length} 个分块</span>
        <span>·</span>
        <span>总 tokens: {totalTokens.toLocaleString()}</span>
      </div>
      <div className="max-h-80 space-y-2 overflow-y-auto">
        {chunks.map((chunk) => (
          <div
            key={chunk.id}
            className="rounded border border-border-subtle bg-background p-3"
          >
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-xs font-medium text-secondary">
                #{chunk.chunk_index}
              </span>
              <span className="text-xs text-tertiary">
                {chunk.token_count} tokens
              </span>
            </div>
            <p className="whitespace-pre-wrap break-words text-xs leading-relaxed text-foreground">
              {chunk.content}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}
