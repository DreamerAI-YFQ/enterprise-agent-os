import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import { Spinner, Badge, cn, toast } from "@eaos/shared";
import {
  GitBranch,
  Sparkles,
  ArrowRight,
  CheckCircle2,
  XCircle,
  Clock,
  ShieldCheck,
  FlaskConical,
  TrendingUp,
} from "lucide-react";

interface Skill {
  id: string;
  tenant_id: string;
  scope: string;
  owner_id: string;
  name: string;
  display_name: string;
  description: string;
  category: string;
  risk_level: string;
  instructions: string;
  tools: string[];
  version: number;
  status: string;
}

type Tab = "candidates" | "path" | "benchmark";

export default function PromotionsPage() {
  const [tab, setTab] = useState<Tab>("candidates");

  const skillsQuery = useQuery({
    queryKey: ["admin", "skills"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/skills", {});
      if (error || !data) return [] as Skill[];
      return data as unknown as Skill[];
    },
  });

  const skills = skillsQuery.data ?? [];

  // Promotion candidates: skills with status "published" (user-published, awaiting admin review)
  const candidates = skills.filter((s) => s.status === "published");
  // Deprecated skills (rejected promotions)
  const deprecated = skills.filter((s) => s.status === "deprecated");
  // Draft skills
  const drafts = skills.filter((s) => s.status === "draft");

  const handlePromote = (skill: Skill) => {
    toast.show({
      title: "需要后端支持",
      description: `技能晋升 API (/admin/skills/${skill.id?.slice(0, 8) ?? "?"}/promote) 尚未实现。当前仅支持查看技能状态。`,
      variant: "danger",
    });
  };

  const handleReject = (skill: Skill) => {
    toast.show({
      title: "需要后端支持",
      description: `技能驳回需要后端晋升审批 API。可使用 /admin/skills/${skill.id?.slice(0, 8) ?? "?"}/deprecate 降级技能。`,
      variant: "danger",
    });
  };

  const tabs: { value: Tab; label: string; icon: typeof GitBranch }[] = [
    { value: "candidates", label: "晋升候选", icon: Sparkles },
    { value: "path", label: "晋升路径", icon: GitBranch },
    { value: "benchmark", label: "安全基准", icon: ShieldCheck },
  ];

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <h1 className="text-2xl font-semibold text-foreground">技能晋升</h1>
        <p className="mt-1 text-sm text-secondary">
          审查员工发布的技能，评估是否晋升为租户级共享技能
        </p>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-border-subtle px-8">
        {tabs.map((t) => (
          <button
            key={t.value}
            onClick={() => setTab(t.value)}
            className={cn(
              "flex items-center gap-1.5 border-b-2 px-3 py-2.5 text-sm font-medium transition-colors",
              tab === t.value
                ? "border-accent text-accent"
                : "border-transparent text-secondary hover:text-foreground"
            )}
          >
            <t.icon className="h-3.5 w-3.5" />
            {t.label}
            {t.value === "candidates" && candidates.length > 0 && (
              <span className="rounded-full bg-accent px-1.5 py-0.5 text-xs text-white">
                {candidates.length}
              </span>
            )}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6">
        {tab === "candidates" ? (
          skillsQuery.isLoading ? (
            <div className="flex h-40 items-center justify-center">
              <Spinner />
            </div>
          ) : candidates.length === 0 ? (
            <div className="flex h-full items-center justify-center">
              <div className="flex flex-col items-center gap-3 text-center">
                <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                  <Sparkles className="h-8 w-8" strokeWidth={1.5} />
                </div>
                <h3 className="text-lg font-medium text-foreground">暂无晋升候选</h3>
                <p className="max-w-sm text-sm text-secondary">
                  员工发布技能后（status=published），将在此显示等待管理员审查
                </p>
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              {candidates.map((skill) => (
                <div
                  key={skill.id}
                  className="rounded-md border border-border bg-elevated p-5 shadow-sm"
                >
                  <div className="flex items-start justify-between">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <h3 className="text-sm font-semibold text-foreground">
                          {skill.display_name || skill.name}
                        </h3>
                        <Badge variant="outline" className="text-xs">
                          v{skill.version}
                        </Badge>
                        <Badge
                          variant="outline"
                          className={cn(
                            "text-xs",
                            skill.risk_level === "high" && "border-danger/30 text-danger",
                            skill.risk_level === "medium" && "border-warning/30 text-warning",
                            skill.risk_level === "low" && "border-success/30 text-success"
                          )}
                        >
                          {skill.risk_level} 风险
                        </Badge>
                        <Badge variant="secondary" className="text-xs">
                          {skill.category}
                        </Badge>
                      </div>
                      <p className="mt-1.5 text-sm text-secondary">
                        {skill.description}
                      </p>
                      <div className="mt-2 flex items-center gap-3 text-xs text-tertiary">
                        <span>所有者: {skill.owner_id?.slice(0, 8) ?? "—"}</span>
                        <span>·</span>
                        <span>工具: {(skill.tools ?? []).length > 0 ? (skill.tools ?? []).join(", ") : "无"}</span>
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <button
                        onClick={() => handlePromote(skill)}
                        className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-strong"
                      >
                        <CheckCircle2 className="h-3.5 w-3.5" />
                        晋升
                      </button>
                      <button
                        onClick={() => handleReject(skill)}
                        className="flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs font-medium text-secondary transition-colors hover:bg-subtle hover:text-danger"
                      >
                        <XCircle className="h-3.5 w-3.5" />
                        驳回
                      </button>
                    </div>
                  </div>

                  {/* Instructions Preview */}
                  {skill.instructions && (
                    <div className="mt-3 rounded-md border border-border-subtle bg-subtle/30 p-3">
                      <p className="mb-1 text-xs font-medium text-tertiary">指令预览</p>
                      <p className="line-clamp-3 text-xs text-secondary">
                        {skill.instructions}
                      </p>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )
        ) : tab === "path" ? (
          /* Skill Lifecycle Path Visualization */
          <div className="flex h-full items-center justify-center">
            <div className="w-full max-w-3xl">
              <div className="rounded-md border border-border bg-elevated p-8 shadow-sm">
                <h3 className="mb-6 text-sm font-medium text-foreground">技能晋升路径</h3>
                <div className="flex items-center justify-between">
                  {/* Draft */}
                  <div className="flex flex-col items-center gap-2">
                    <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                      <Clock className="h-7 w-7" strokeWidth={1.5} />
                    </div>
                    <div className="text-center">
                      <p className="text-sm font-medium text-foreground">草稿</p>
                      <p className="text-xs text-tertiary">{drafts.length} 个</p>
                    </div>
                  </div>

                  <div className="flex flex-1 items-center justify-center px-2">
                    <ArrowRight className="h-4 w-4 text-tertiary" />
                    <span className="ml-1 text-xs text-tertiary">员工发布</span>
                  </div>

                  {/* Published */}
                  <div className="flex flex-col items-center gap-2">
                    <div className="flex h-16 w-16 items-center justify-center rounded-full bg-warning/10 text-warning">
                      <Sparkles className="h-7 w-7" strokeWidth={1.5} />
                    </div>
                    <div className="text-center">
                      <p className="text-sm font-medium text-foreground">已发布</p>
                      <p className="text-xs text-tertiary">{candidates.length} 个</p>
                    </div>
                  </div>

                  <div className="flex flex-1 items-center justify-center px-2">
                    <ArrowRight className="h-4 w-4 text-tertiary" />
                    <span className="ml-1 text-xs text-tertiary">管理员审批</span>
                  </div>

                  {/* Promoted */}
                  <div className="flex flex-col items-center gap-2">
                    <div className="flex h-16 w-16 items-center justify-center rounded-full bg-success/10 text-success">
                      <ShieldCheck className="h-7 w-7" strokeWidth={1.5} />
                    </div>
                    <div className="text-center">
                      <p className="text-sm font-medium text-foreground">共享技能</p>
                      <p className="text-xs text-tertiary">租户级</p>
                    </div>
                  </div>

                  <div className="flex flex-1 items-center justify-center px-2">
                    <ArrowRight className="h-4 w-4 text-tertiary" />
                    <span className="ml-1 text-xs text-tertiary">降级/废弃</span>
                  </div>

                  {/* Deprecated */}
                  <div className="flex flex-col items-center gap-2">
                    <div className="flex h-16 w-16 items-center justify-center rounded-full bg-danger/10 text-danger">
                      <XCircle className="h-7 w-7" strokeWidth={1.5} />
                    </div>
                    <div className="text-center">
                      <p className="text-sm font-medium text-foreground">已废弃</p>
                      <p className="text-xs text-tertiary">{deprecated.length} 个</p>
                    </div>
                  </div>
                </div>

                <div className="mt-8 rounded-md border border-border-subtle bg-subtle/30 p-4">
                  <p className="text-xs text-secondary">
                    <strong className="text-foreground">晋升流程：</strong>
                    员工创建技能（草稿）→ 员工发布（已发布，等待审查）→
                    管理员审查并晋升（共享技能）或驳回（废弃）。晋升后的技能对租户内所有用户可见可用。
                  </p>
                </div>
              </div>
            </div>
          </div>
        ) : (
          /* Safety Benchmark Placeholder */
          <div className="space-y-4">
            <div className="rounded-md border border-border bg-elevated p-6 shadow-sm">
              <div className="flex items-center gap-2">
                <FlaskConical className="h-5 w-5 text-accent" />
                <h3 className="text-sm font-medium text-foreground">影子测试</h3>
                <Badge variant="outline" className="ml-auto text-xs text-tertiary">
                  即将上线
                </Badge>
              </div>
              <p className="mt-3 text-sm text-secondary">
                影子测试在真实流量中并行运行候选技能与当前技能，对比输出差异，
                评估晋升安全性。此功能需要后端影子测试引擎支持。
              </p>
              <div className="mt-4 grid grid-cols-3 gap-3">
                <div className="rounded-md border border-border-subtle bg-subtle/20 p-3 text-center">
                  <p className="text-lg font-semibold text-tertiary">—</p>
                  <p className="mt-1 text-xs text-tertiary">测试运行数</p>
                </div>
                <div className="rounded-md border border-border-subtle bg-subtle/20 p-3 text-center">
                  <p className="text-lg font-semibold text-tertiary">—</p>
                  <p className="mt-1 text-xs text-tertiary">一致率</p>
                </div>
                <div className="rounded-md border border-border-subtle bg-subtle/20 p-3 text-center">
                  <p className="text-lg font-semibold text-tertiary">—</p>
                  <p className="mt-1 text-xs text-tertiary">通过率</p>
                </div>
              </div>
            </div>

            <div className="rounded-md border border-border bg-elevated p-6 shadow-sm">
              <div className="flex items-center gap-2">
                <ShieldCheck className="h-5 w-5 text-accent" />
                <h3 className="text-sm font-medium text-foreground">安全基准报告</h3>
                <Badge variant="outline" className="ml-auto text-xs text-tertiary">
                  即将上线
                </Badge>
              </div>
              <p className="mt-3 text-sm text-secondary">
                安全基准报告评估技能在提示注入、数据泄露、权限越界等维度的安全性，
                确保晋升后的技能不会引入安全风险。
              </p>
              <div className="mt-4 space-y-2">
                {[
                  "提示注入防护",
                  "数据泄露检测",
                  "权限越界检查",
                  "工具调用安全",
                  "输出内容过滤",
                ].map((item) => (
                  <div
                    key={item}
                    className="flex items-center justify-between rounded-md border border-border-subtle bg-subtle/20 px-3 py-2"
                  >
                    <span className="text-xs text-secondary">{item}</span>
                    <span className="text-xs text-tertiary">未评估</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="rounded-md border border-border bg-elevated p-6 shadow-sm">
              <div className="flex items-center gap-2">
                <TrendingUp className="h-5 w-5 text-accent" />
                <h3 className="text-sm font-medium text-foreground">技能质量指标</h3>
              </div>
              <p className="mt-3 text-sm text-secondary">
                质量指标从调用频率、成功率、用户评分、错误率等维度量化评估技能表现。
                晋升前需确保候选技能在各维度达标。
              </p>
              <div className="mt-4 grid grid-cols-4 gap-3">
                <div className="rounded-md border border-border-subtle bg-subtle/20 p-3 text-center">
                  <p className="text-lg font-semibold text-tertiary">—</p>
                  <p className="mt-1 text-xs text-tertiary">调用次数</p>
                </div>
                <div className="rounded-md border border-border-subtle bg-subtle/20 p-3 text-center">
                  <p className="text-lg font-semibold text-tertiary">—</p>
                  <p className="mt-1 text-xs text-tertiary">成功率</p>
                </div>
                <div className="rounded-md border border-border-subtle bg-subtle/20 p-3 text-center">
                  <p className="text-lg font-semibold text-tertiary">—</p>
                  <p className="mt-1 text-xs text-tertiary">平均评分</p>
                </div>
                <div className="rounded-md border border-border-subtle bg-subtle/20 p-3 text-center">
                  <p className="text-lg font-semibold text-tertiary">—</p>
                  <p className="mt-1 text-xs text-tertiary">错误率</p>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
