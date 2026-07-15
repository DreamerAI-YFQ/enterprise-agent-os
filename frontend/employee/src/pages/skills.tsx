import { useState, useMemo } from "react";
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
  Sparkles,
  Plus,
  Wand2,
  Shield,
  Send,
  X,
  Pencil,
  Ban,
  Trash2,
  Check,
  Building2,
  Users,
  User,
} from "lucide-react";

interface Skill {
  id: string;
  scope: string;
  owner_id: string | null;
  name: string;
  display_name: string;
  description: string;
  category: string;
  risk_level: string;
  instructions: string;
  tools: string[];
  version: number | string;
  status: string;
}

const CATEGORY_LABELS: Record<string, string> = {
  knowledge_api: "知识 API",
  verification: "业务校验",
  data_analysis: "数据分析",
  process_automation: "流程自动化",
  document_template: "文档模板",
  quality_review: "质量审核",
  system_operation: "系统操作",
  runbook: "运维手册",
  infra_ops: "基础设施",
};

const RISK_LABELS: Record<string, string> = {
  low: "低风险",
  medium: "中风险",
  high: "高风险",
};

const RISK_COLORS: Record<string, string> = {
  low: "text-success",
  medium: "text-warning",
  high: "text-danger",
};

const STATUS_LABELS: Record<string, string> = {
  draft: "草稿",
  published: "已发布",
  deprecated: "已废弃",
};

const SCOPE_LABELS: Record<string, string> = {
  personal: "个人",
  department: "部门",
  company: "公司",
};

const SCOPE_ICONS: Record<string, typeof User> = {
  personal: User,
  department: Users,
  company: Building2,
};

const SCOPE_COLORS: Record<string, string> = {
  personal: "bg-accent-subtle text-accent",
  department: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300",
  company: "bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300",
};

type ScopeFilter = "all" | "personal" | "department" | "company";

export default function SkillsPage() {
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [selectedSkill, setSelectedSkill] = useState<Skill | null>(null);
  const [scopeFilter, setScopeFilter] = useState<ScopeFilter>("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [deprecatingId, setDeprecatingId] = useState<string | null>(null);
  const [deprecateReason, setDeprecateReason] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["skills", scopeFilter],
    queryFn: async () => {
      const params =
        scopeFilter !== "all" ? { params: { query: { scope: scopeFilter } } } : {};
      const { data, error } = await apiClient.GET("/skills", params);
      if (error || !data) return [] as Skill[];
      return data as unknown as Skill[];
    },
  });

  const publishMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiClient.POST("/skills/{skill_id}/publish", {
        params: { path: { skill_id: id } },
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
      toast.show({ title: "技能已发布", variant: "success" });
    },
  });

  const deprecateMutation = useMutation({
    mutationFn: async ({ id, reason }: { id: string; reason: string }) => {
      await apiClient.POST("/skills/{skill_id}/deprecate", {
        params: { path: { skill_id: id } },
        body: { reason },
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
      setDeprecatingId(null);
      setDeprecateReason("");
      toast.show({ title: "技能已废弃", variant: "success" });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "废弃失败";
      toast.show({ title: msg, variant: "danger" });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiClient.DELETE("/skills/{skill_id}", {
        params: { path: { skill_id: id } },
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["skills"] });
      setConfirmDeleteId(null);
      toast.show({ title: "技能已删除", variant: "success" });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "删除失败";
      toast.show({ title: msg, variant: "danger" });
    },
  });

  const allSkills = query.data ?? [];
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return allSkills;
    return allSkills.filter(
      (s) =>
        s.display_name.toLowerCase().includes(q) ||
        s.name.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q),
    );
  }, [allSkills, search]);
  const PAGE_SIZE = 12;
  const total = filtered.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const skills = useMemo(() => {
    const start = (safePage - 1) * PAGE_SIZE;
    return filtered.slice(start, start + PAGE_SIZE);
  }, [filtered, safePage]);

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">技能库</h1>
            <p className="mt-1 text-sm text-secondary">
              管理 Agent 可调用的技能（个人 / 部门 / 公司）
            </p>
          </div>
          <Button onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4" />
            创建个人技能
          </Button>
        </div>

        {/* Search + Scope filter */}
        <div className="mt-4 flex items-center gap-3">
          <SearchInput
            value={search}
            onChange={(v) => {
              setSearch(v);
              setPage(1);
            }}
            placeholder="搜索技能名称或描述..."
            className="max-w-xs"
          />
          <div className="flex gap-1 rounded-md bg-subtle p-1">
            {(["all", "personal", "department", "company"] as ScopeFilter[]).map(
              (s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => {
                    setScopeFilter(s);
                    setPage(1);
                  }}
                  className={cn(
                    "rounded px-3 py-1.5 text-xs font-medium transition-colors",
                    scopeFilter === s
                      ? "bg-background text-foreground shadow-sm"
                      : "text-secondary hover:text-foreground",
                  )}
                >
                  {s === "all" ? "全部" : SCOPE_LABELS[s]}
                </button>
              ),
            )}
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-8 pb-8">
        {query.isLoading ? (
          <LoadingState />
        ) : skills.length === 0 ? (
          <EmptyState
            icon={Sparkles}
            title="暂无技能"
            description={search || scopeFilter !== "all" ? "未找到匹配的技能" : "创建一个技能，让 Agent 学会执行你的特定任务"}
            action={
              !search && scopeFilter === "all" ? (
                <Button variant="ghost" onClick={() => setShowCreate(true)}>
                  <Wand2 className="h-4 w-4" />
                  创建第一个技能
                </Button>
              ) : undefined
            }
          />
        ) : (
          <div className="space-y-3">
            {skills.map((skill) => {
              const ScopeIcon = SCOPE_ICONS[skill.scope] ?? User;
              return (
                <div
                  key={skill.id}
                  className="rounded-md border border-border bg-elevated p-4 shadow-sm transition-shadow hover:shadow-md"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div
                      className="min-w-0 flex-1 cursor-pointer"
                      onClick={() => setSelectedSkill(skill)}
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium text-foreground">
                          {skill.display_name}
                        </span>
                        <span
                          className={cn(
                            "rounded-full px-2 py-0.5 text-xs",
                            skill.status === "published"
                              ? "bg-success/10 text-success"
                              : skill.status === "draft"
                                ? "bg-warning/10 text-warning"
                                : "bg-tertiary/10 text-tertiary"
                          )}
                        >
                          {STATUS_LABELS[skill.status] ?? skill.status}
                        </span>
                        <span
                          className={cn(
                            "flex items-center gap-1 rounded-full px-2 py-0.5 text-xs",
                            SCOPE_COLORS[skill.scope] ??
                              "bg-subtle text-tertiary"
                          )}
                        >
                          <ScopeIcon className="h-3 w-3" />
                          {SCOPE_LABELS[skill.scope] ?? skill.scope}
                        </span>
                        <span
                          className={cn(
                            "flex items-center gap-0.5 text-xs",
                            RISK_COLORS[skill.risk_level] ?? "text-tertiary"
                          )}
                        >
                          <Shield className="h-3 w-3" />
                          {RISK_LABELS[skill.risk_level] ?? skill.risk_level}
                        </span>
                      </div>
                      <p className="mt-1 text-sm text-secondary line-clamp-2">
                        {skill.description}
                      </p>
                      <div className="mt-2 flex items-center gap-2">
                        <span className="rounded bg-subtle px-1.5 py-0.5 text-xs text-tertiary">
                          {CATEGORY_LABELS[skill.category] ?? skill.category}
                        </span>
                        <span className="text-xs text-tertiary">
                          v{skill.version}
                        </span>
                        <span className="text-xs text-tertiary">
                          @{skill.name}
                        </span>
                      </div>
                    </div>
                    <div className="flex flex-col gap-1">
                      {skill.status === "draft" &&
                        skill.scope === "personal" && (
                          <>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => publishMutation.mutate(skill.id)}
                              disabled={publishMutation.isPending}
                            >
                              <Send className="h-3.5 w-3.5" />
                              发布
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="text-danger hover:text-danger"
                              onClick={() => setConfirmDeleteId(skill.id)}
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                              删除
                            </Button>
                          </>
                        )}
                      {skill.status === "published" &&
                        skill.scope === "personal" && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="text-warning hover:text-warning"
                            onClick={() => setDeprecatingId(skill.id)}
                          >
                            <Ban className="h-3.5 w-3.5" />
                            废弃
                          </Button>
                        )}
                    </div>
                  </div>

                  {deprecatingId === skill.id && (
                    <div className="mt-3 rounded-md bg-subtle/50 p-3">
                      <input
                        type="text"
                        placeholder="废弃原因（可选）..."
                        value={deprecateReason}
                        onChange={(e) => setDeprecateReason(e.target.value)}
                        className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-xs text-foreground placeholder:text-tertiary focus:border-accent focus:outline-none"
                      />
                      <div className="mt-2 flex justify-end gap-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          type="button"
                          onClick={() => {
                            setDeprecatingId(null);
                            setDeprecateReason("");
                          }}
                        >
                          取消
                        </Button>
                        <Button
                          size="sm"
                          type="button"
                          variant="danger"
                          onClick={() =>
                            deprecateMutation.mutate({
                              id: skill.id,
                              reason: deprecateReason,
                            })
                          }
                          disabled={deprecateMutation.isPending}
                        >
                          <Check className="h-3.5 w-3.5" />
                          确认废弃
                        </Button>
                      </div>
                    </div>
                  )}

                  {confirmDeleteId === skill.id && (
                    <div className="mt-3 flex items-center justify-between rounded-md bg-danger/5 px-3 py-2">
                      <span className="text-xs text-foreground">
                        确认删除这个草稿技能？此操作不可撤销。
                      </span>
                      <div className="flex gap-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          type="button"
                          onClick={() => setConfirmDeleteId(null)}
                        >
                          取消
                        </Button>
                        <Button
                          size="sm"
                          type="button"
                          variant="danger"
                          onClick={() => deleteMutation.mutate(skill.id)}
                          disabled={deleteMutation.isPending}
                        >
                          <Check className="h-3.5 w-3.5" />
                          确认删除
                        </Button>
                      </div>
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
        <CreateSkillModal
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            setShowCreate(false);
            void queryClient.invalidateQueries({ queryKey: ["skills"] });
          }}
        />
      )}

      {selectedSkill && (
        <SkillDetailModal
          skill={selectedSkill}
          onClose={() => setSelectedSkill(null)}
          onUpdated={() => {
            void queryClient.invalidateQueries({ queryKey: ["skills"] });
          }}
        />
      )}
    </div>
  );
}

function CreateSkillModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [category, setCategory] = useState("knowledge_api");
  const [riskLevel, setRiskLevel] = useState("low");
  const [instructions, setInstructions] = useState("");

  const createMutation = useMutation({
    mutationFn: async () => {
      await apiClient.POST("/skills", {
        body: {
          name,
          display_name: displayName,
          description,
          category,
          risk_level: riskLevel,
          instructions,
          tools: [],
          scope: "personal",
        },
      });
    },
    onSuccess: () => {
      toast.show({ title: "技能已创建", variant: "success" });
      onCreated();
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "创建失败";
      toast.show({ title: msg, variant: "danger" });
    },
  });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    createMutation.mutate();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="w-full max-w-lg rounded-lg bg-elevated p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-foreground">创建个人技能</h2>
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
              名称（唯一标识，用于 @提及）
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：summarize_report"
              required
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-tertiary focus:border-accent focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary">
              显示名称
            </label>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="例如：报告总结"
              required
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-tertiary focus:border-accent focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary">
              描述
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="描述这个技能的用途..."
              required
              rows={2}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-tertiary focus:border-accent focus:outline-none"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-medium text-secondary">
                分类
              </label>
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none"
              >
                {Object.entries(CATEGORY_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-secondary">
                风险等级
              </label>
              <select
                value={riskLevel}
                onChange={(e) => setRiskLevel(e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none"
              >
                {Object.entries(RISK_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary">
              指令（Prompt，告诉 Agent 如何执行）
            </label>
            <textarea
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              placeholder="给 Agent 的指令，描述如何执行这个技能..."
              rows={4}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-tertiary focus:border-accent focus:outline-none"
            />
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" type="button" onClick={onClose}>
              取消
            </Button>
            <Button type="submit" disabled={createMutation.isPending}>
              {createMutation.isPending ? "创建中..." : "创建"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

function SkillDetailModal({
  skill,
  onClose,
  onUpdated,
}: {
  skill: Skill;
  onClose: () => void;
  onUpdated: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [displayName, setDisplayName] = useState(skill.display_name);
  const [description, setDescription] = useState(skill.description);
  const [instructions, setInstructions] = useState(skill.instructions);

  const canEdit = skill.scope === "personal";

  const updateMutation = useMutation({
    mutationFn: async () => {
      await apiClient.PUT("/skills/{skill_id}", {
        params: { path: { skill_id: skill.id } },
        body: {
          display_name: displayName,
          description,
          instructions,
        },
      });
    },
    onSuccess: () => {
      toast.show({ title: "已保存", variant: "success" });
      setEditing(false);
      onUpdated();
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "保存失败";
      toast.show({ title: msg, variant: "danger" });
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="w-full max-w-lg rounded-lg bg-elevated p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-foreground">
            {editing ? "编辑技能" : skill.display_name}
          </h2>
          <div className="flex items-center gap-2">
            {canEdit && !editing && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setEditing(true)}
              >
                <Pencil className="h-3.5 w-3.5" />
                编辑
              </Button>
            )}
            <button
              type="button"
              onClick={onClose}
              className="rounded-md p-1 text-tertiary hover:bg-subtle hover:text-foreground"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        {!editing ? (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <span
                className={cn(
                  "rounded-full px-2 py-0.5 text-xs",
                  skill.status === "published"
                    ? "bg-success/10 text-success"
                    : "bg-warning/10 text-warning"
                )}
              >
                {STATUS_LABELS[skill.status] ?? skill.status}
              </span>
              <span
                className={cn(
                  "flex items-center gap-1 rounded-full px-2 py-0.5 text-xs",
                  SCOPE_COLORS[skill.scope] ?? "bg-subtle text-tertiary"
                )}
              >
                {SCOPE_LABELS[skill.scope] ?? skill.scope}
              </span>
              <span
                className={cn(
                  "flex items-center gap-0.5 text-xs",
                  RISK_COLORS[skill.risk_level] ?? "text-tertiary"
                )}
              >
                <Shield className="h-3 w-3" />
                {RISK_LABELS[skill.risk_level] ?? skill.risk_level}
              </span>
              <span className="rounded bg-subtle px-1.5 py-0.5 text-xs text-tertiary">
                {CATEGORY_LABELS[skill.category] ?? skill.category}
              </span>
              <span className="text-xs text-tertiary">v{skill.version}</span>
              <span className="text-xs text-tertiary">@{skill.name}</span>
            </div>
            <p className="text-sm text-secondary">{skill.description}</p>
            {skill.instructions && (
              <div>
                <p className="mb-1 text-xs font-medium text-secondary">指令</p>
                <pre className="whitespace-pre-wrap rounded-md bg-subtle/50 p-3 text-xs text-foreground">
                  {skill.instructions}
                </pre>
              </div>
            )}
            {skill.tools.length > 0 && (
              <div>
                <p className="mb-1 text-xs font-medium text-secondary">绑定工具</p>
                <div className="flex flex-wrap gap-1">
                  {skill.tools.map((tool) => (
                    <span
                      key={tool}
                      className="rounded bg-accent-subtle px-1.5 py-0.5 text-xs text-accent"
                    >
                      {tool}
                    </span>
                  ))}
                </div>
              </div>
            )}
            {!canEdit && (
              <p className="text-xs text-tertiary">
                此技能为{SCOPE_LABELS[skill.scope]}级，仅创建者或管理员可编辑。
              </p>
            )}
          </div>
        ) : (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              updateMutation.mutate();
            }}
            className="space-y-3"
          >
            <div>
              <label className="mb-1 block text-xs font-medium text-secondary">
                显示名称
              </label>
              <input
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-accent focus:outline-none"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-secondary">
                描述
              </label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={2}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-accent focus:outline-none"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-secondary">
                指令（Prompt）
              </label>
              <textarea
                value={instructions}
                onChange={(e) => setInstructions(e.target.value)}
                rows={6}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-accent focus:outline-none"
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button
                variant="ghost"
                type="button"
                onClick={() => {
                  setEditing(false);
                  setDisplayName(skill.display_name);
                  setDescription(skill.description);
                  setInstructions(skill.instructions);
                }}
              >
                取消
              </Button>
              <Button type="submit" disabled={updateMutation.isPending}>
                {updateMutation.isPending ? "保存中..." : "保存"}
              </Button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
