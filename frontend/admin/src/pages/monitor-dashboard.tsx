import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import { Spinner, cn } from "@eaos/shared";
import {
  Clock,
  Activity,
  DollarSign,
  Sparkles,
  TrendingUp,
  Bot,
  Users,
  CheckCircle2,
} from "lucide-react";

interface SpanOverview {
  tenant_id: string;
  total_agents: number;
  active_users_today: number;
  total_tokens_today: number;
  total_cost_usd_today: number;
  top_skills: { name: string; count: number }[];
  task_success_rate: number;
}

interface MetricsData {
  counts: {
    users: number;
    agents: number;
    sessions: number;
    skills: number;
    documents: number;
    pending_approvals: number;
    unread_notifications: number;
  };
  activity_7d: { day: string; sessions: number }[];
}

export default function MonitorDashboardPage() {
  const now = new Date();
  const start24h = new Date(now.getTime() - 24 * 60 * 60 * 1000);

  const overview24h = useQuery({
    queryKey: ["admin", "spans", "overview", start24h.toISOString(), now.toISOString()],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/spans/overview", {
        params: {
          query: { start: start24h.toISOString(), end: now.toISOString() },
        },
      });
      if (error || !data) return null;
      return data as unknown as SpanOverview;
    },
    refetchInterval: 30_000,
  });

  const metricsQuery = useQuery({
    queryKey: ["admin", "metrics"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/metrics", {});
      if (error || !data) return null;
      return data as unknown as MetricsData;
    },
    refetchInterval: 30_000,
  });

  const overview = overview24h.data;
  const metrics = metricsQuery.data;

  // Only block on the very first load when no data is available yet.
  // Once any query returns, render the page with null-safe defaults so a
  // hanging/erroring /admin/spans/overview won't keep the page spinning.
  if (
    !overview &&
    !metrics &&
    (overview24h.isLoading || metricsQuery.isLoading)
  ) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  }

  const activity = metrics?.activity_7d ?? [];
  const maxSessions = Math.max(...activity.map((a) => a.sessions), 1);
  const topSkills = overview?.top_skills ?? [];
  const maxSkillCount = Math.max(...topSkills.map((s) => s.count), 1);
  const successRate = overview?.task_success_rate ?? 0;
  const isHealthy = successRate >= 0.9;
  const isWarning = successRate >= 0.7 && successRate < 0.9;

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <h1 className="text-2xl font-semibold text-foreground">可观测性仪表盘</h1>
        <p className="mt-1 text-sm text-secondary">
          系统健康度、性能、成本与技能调用概览 · 30 秒自动刷新
        </p>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6">
        {/* Panel 1: System Health */}
        <div className="mb-6 grid grid-cols-3 gap-4">
          <div className="rounded-md border border-border bg-elevated p-4 shadow-sm">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-tertiary">
                <CheckCircle2 className="h-4 w-4" />
                <span className="text-xs font-medium">任务成功率</span>
              </div>
              {isHealthy ? (
                <span className="rounded bg-success/10 px-1.5 py-0.5 text-xs text-success">健康</span>
              ) : isWarning ? (
                <span className="rounded bg-warning/10 px-1.5 py-0.5 text-xs text-warning">关注</span>
              ) : (
                <span className="rounded bg-danger/10 px-1.5 py-0.5 text-xs text-danger">异常</span>
              )}
            </div>
            <p className="mt-2 text-2xl font-semibold text-foreground">
              {(successRate * 100).toFixed(1)}%
            </p>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-subtle">
              <div
                className={cn(
                  "h-full rounded-full transition-all duration-base",
                  isHealthy ? "bg-success" : isWarning ? "bg-warning" : "bg-danger"
                )}
                style={{ width: `${Math.max(successRate * 100, 2)}%` }}
              />
            </div>
          </div>

          <div className="rounded-md border border-border bg-elevated p-4 shadow-sm">
            <div className="flex items-center gap-2 text-tertiary">
              <Bot className="h-4 w-4" />
              <span className="text-xs font-medium">活跃 Agent</span>
            </div>
            <p className="mt-2 text-2xl font-semibold text-foreground">
              {overview?.total_agents ?? 0}
            </p>
            <p className="mt-1 text-xs text-tertiary">24 小时内</p>
          </div>

          <div className="rounded-md border border-border bg-elevated p-4 shadow-sm">
            <div className="flex items-center gap-2 text-tertiary">
              <Users className="h-4 w-4" />
              <span className="text-xs font-medium">活跃用户</span>
            </div>
            <p className="mt-2 text-2xl font-semibold text-foreground">
              {overview?.active_users_today ?? 0}
            </p>
            <p className="mt-1 text-xs text-tertiary">今日</p>
          </div>
        </div>

        {/* Panel 2: Performance (Token + Cost) */}
        <div className="mb-6 grid grid-cols-2 gap-4">
          <div className="rounded-md border border-border bg-elevated p-6 shadow-sm">
            <div className="mb-4 flex items-center gap-2">
              <Clock className="h-4 w-4 text-accent" />
              <h2 className="text-sm font-medium text-foreground">Token 消耗</h2>
            </div>
            <p className="text-3xl font-semibold text-foreground">
              {(overview?.total_tokens_today ?? 0).toLocaleString()}
            </p>
            <p className="mt-1 text-xs text-tertiary">今日总 Token</p>
            <div className="mt-4 space-y-1">
              <div className="flex justify-between text-xs">
                <span className="text-tertiary">输入/输出比</span>
                <span className="text-foreground">—</span>
              </div>
              <div className="flex justify-between text-xs">
                <span className="text-tertiary">平均/会话</span>
                <span className="text-foreground">
                  {overview && overview.total_agents > 0
                    ? Math.round((overview.total_tokens_today / Math.max(overview.total_agents, 1)))
                    : "—"}
                </span>
              </div>
            </div>
          </div>

          <div className="rounded-md border border-border bg-elevated p-6 shadow-sm">
            <div className="mb-4 flex items-center gap-2">
              <DollarSign className="h-4 w-4 text-success" />
              <h2 className="text-sm font-medium text-foreground">成本配额</h2>
            </div>
            <p className="text-3xl font-semibold text-foreground">
              ${(overview?.total_cost_usd_today ?? 0).toFixed(4)}
            </p>
            <p className="mt-1 text-xs text-tertiary">今日总成本 (USD)</p>
            <div className="mt-4 space-y-1">
              <div className="flex justify-between text-xs">
                <span className="text-tertiary">月度预估</span>
                <span className="text-foreground">
                  ${((overview?.total_cost_usd_today ?? 0) * 30).toFixed(2)}
                </span>
              </div>
              <div className="flex justify-between text-xs">
                <span className="text-tertiary">配额上限</span>
                <span className="text-foreground">$100.00/日</span>
              </div>
              <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-subtle">
                <div
                  className={cn(
                    "h-full rounded-full transition-all",
                    (overview?.total_cost_usd_today ?? 0) > 80
                      ? "bg-danger"
                      : (overview?.total_cost_usd_today ?? 0) > 50
                      ? "bg-warning"
                      : "bg-success"
                  )}
                  style={{
                    width: `${Math.min(((overview?.total_cost_usd_today ?? 0) / 100) * 100, 100)}%`,
                  }}
                />
              </div>
            </div>
          </div>
        </div>

        {/* Panel 3: 7-Day Activity Trend */}
        <div className="mb-6 rounded-md border border-border bg-elevated p-6 shadow-sm">
          <div className="mb-4 flex items-center gap-2">
            <TrendingUp className="h-4 w-4 text-accent" />
            <h2 className="text-sm font-medium text-foreground">7 天会话速率</h2>
          </div>
          {activity.length === 0 ? (
            <p className="py-8 text-center text-xs text-tertiary">暂无活动数据</p>
          ) : (
            <div className="flex h-32 items-end gap-3">
              {activity.map((day, idx) => {
                const height = (day.sessions / maxSessions) * 100;
                const date = new Date(day.day);
                return (
                  <div key={idx} className="group flex flex-1 flex-col items-center gap-1.5">
                    <span className="text-xs text-tertiary opacity-0 transition-opacity group-hover:opacity-100">
                      {day.sessions}
                    </span>
                    <div className="flex w-full flex-1 items-end">
                      <div
                        className="w-full rounded-t bg-accent/60 transition-all duration-base hover:bg-accent"
                        style={{ height: `${Math.max(height, 2)}%` }}
                      />
                    </div>
                    <span className="text-xs text-tertiary">
                      {date.getMonth() + 1}/{date.getDate()}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Panel 4: Skill Call Top 5 */}
        <div className="mb-6 rounded-md border border-border bg-elevated p-6 shadow-sm">
          <div className="mb-4 flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-accent" />
            <h2 className="text-sm font-medium text-foreground">技能调用 Top 5</h2>
          </div>
          {topSkills.length === 0 ? (
            <p className="py-8 text-center text-xs text-tertiary">暂无技能调用记录</p>
          ) : (
            <div className="space-y-3">
              {topSkills.slice(0, 5).map((skill, idx) => {
                const width = (skill.count / maxSkillCount) * 100;
                return (
                  <div key={idx} className="flex items-center gap-3">
                    <span className="w-5 text-xs font-medium text-tertiary">
                      {idx + 1}
                    </span>
                    <span className="w-32 truncate text-sm text-foreground">
                      {skill.name}
                    </span>
                    <div className="flex-1">
                      <div className="h-6 overflow-hidden rounded bg-subtle/30">
                        <div
                          className="flex h-full items-center justify-end rounded bg-accent/60 px-2 transition-all duration-base"
                          style={{ width: `${Math.max(width, 5)}%` }}
                        >
                          <span className="text-xs text-white">{skill.count}</span>
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Panel 5: Evolution Stage Distribution */}
        <div className="rounded-md border border-border bg-elevated p-6 shadow-sm">
          <div className="mb-4 flex items-center gap-2">
            <Activity className="h-4 w-4 text-accent" />
            <h2 className="text-sm font-medium text-foreground">系统指标汇总</h2>
          </div>
          <div className="grid grid-cols-4 gap-3">
            {metrics?.counts &&
              Object.entries(metrics.counts).map(([key, value]) => (
                <div
                  key={key}
                  className="rounded-md border border-border-subtle bg-subtle/20 p-3 text-center"
                >
                  <p className="text-xl font-semibold text-foreground">{value}</p>
                  <p className="mt-0.5 text-xs text-tertiary">
                    {key === "pending_approvals"
                      ? "待审批"
                      : key === "unread_notifications"
                      ? "未读通知"
                      : key === "users"
                      ? "用户"
                      : key === "agents"
                      ? "Agent"
                      : key === "sessions"
                      ? "会话"
                      : key === "skills"
                      ? "技能"
                      : key === "documents"
                      ? "文档"
                      : key}
                  </p>
                </div>
              ))}
          </div>
        </div>
      </div>
    </div>
  );
}
