import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { apiClient } from "@eaos/shared/api";
import {
  Button,
  cn,
  toast,
} from "@eaos/shared";
import {
  Bot,
  ShieldCheck,
  Sparkles,
  Activity,
  Cpu,
  Users,
  Database,
  Gauge,
  ArrowRight,
  Download,
} from "lucide-react";
import { relativeTime } from "../lib/relative-time";

interface AdminApproval {
  id: string;
  agent_id: string;
  session_id: string;
  reason: string;
  status: string;
  created_at: string;
}

interface AdminSkill {
  id: string;
  name: string;
  display_name: string;
  status: string;
  risk_level: string;
}

interface AdminAgent {
  id: string;
  name: string;
  description: string;
  scope: string;
  status: string;
}

interface AuditLog {
  id: number;
  action: string;
  resource_type: string;
  created_at: string;
}

interface MetricData {
  counts: {
    users: number;
    agents: number;
    sessions: number;
    skills: number;
    documents: number;
    pending_approvals: number;
    unread_notifications: number;
  };
  activity: { bucket: string; sessions: number }[];
  time_range: { start: string; end: string };
  granularity: string;
}

type TimeRange = "today" | "7d" | "30d";

const RANGE_LABELS: Record<TimeRange, string> = {
  today: "今日",
  "7d": "7 天",
  "30d": "30 天",
};

function rangeToParams(range: TimeRange): { start_time: string; granularity: string } {
  const now = new Date();
  if (range === "today") {
    const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    return { start_time: start.toISOString(), granularity: "hour" };
  }
  if (range === "7d") {
    const start = new Date(now.getTime() - 7 * 24 * 60 * 60 * 1000);
    return { start_time: start.toISOString(), granularity: "day" };
  }
  const start = new Date(now.getTime() - 30 * 24 * 60 * 60 * 1000);
  return { start_time: start.toISOString(), granularity: "day" };
}

export default function Dashboard() {
  const [timeRange, setTimeRange] = useState<TimeRange>("7d");

  const approvalsQuery = useQuery({
    queryKey: ["admin", "approvals"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/approvals", {});
      if (error || !data) return [] as AdminApproval[];
      return (data as unknown as { items: AdminApproval[] }).items ?? [];
    },
  });

  const skillsQuery = useQuery({
    queryKey: ["admin", "skills"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/skills", {});
      if (error || !data) return [] as AdminSkill[];
      return data as unknown as AdminSkill[];
    },
  });

  const agentsQuery = useQuery({
    queryKey: ["agents"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/agents", {});
      if (error || !data) return [] as AdminAgent[];
      return data as unknown as AdminAgent[];
    },
  });

  const auditQuery = useQuery({
    queryKey: ["admin", "audit-logs"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/audit-logs", {
        params: { query: { limit: 10 } },
      });
      if (error || !data) return [] as AuditLog[];
      return (data as unknown as { items: AuditLog[] }).items ?? [];
    },
  });

  const rangeParams = rangeToParams(timeRange);

  const metricsQuery = useQuery({
    queryKey: ["admin", "metrics", timeRange],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/metrics", {
        params: { query: rangeParams },
      });
      if (error || !data) return null;
      return data as unknown as MetricData;
    },
  });

  const pendingApprovals = (approvalsQuery.data ?? []).filter(
    (a) => a.status === "pending",
  );
  const skills = skillsQuery.data ?? [];
  const agents = agentsQuery.data ?? [];
  const auditLogs = auditQuery.data ?? [];
  const metrics = metricsQuery.data;

  const handleExport = async () => {
    try {
      const { data, error } = await apiClient.GET("/admin/metrics/export", {
        params: { query: rangeParams },
        parseAs: "text",
      });
      if (error || !data) throw new Error("Export failed");
      const blob = new Blob([data], { type: "text/csv" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "metrics.csv";
      a.click();
      URL.revokeObjectURL(url);
      toast.show({ title: "导出成功", variant: "success" });
    } catch {
      toast.show({ title: "导出失败", variant: "danger" });
    }
  };

  const stats = [
    { label: "Agent 数", value: agents.length, icon: Bot },
    { label: "待审批", value: pendingApprovals.length, icon: ShieldCheck },
    { label: "技能数", value: skills.length, icon: Sparkles },
    {
      label: "审计记录",
      value: auditLogs.length,
      icon: Activity,
    },
  ];

  const shortcuts = [
    { label: "Agent 管理", desc: "配置 Agent 能力与权限", to: "/admin/agents", icon: Bot },
    { label: "审批管理", desc: "处理待审批申请", to: "/admin/approvals", icon: ShieldCheck },
    { label: "用户管理", desc: "管理用户与角色", to: "/admin/users", icon: Users },
    { label: "模型管理", desc: "LLM Provider 配置", to: "/admin/models", icon: Cpu },
    { label: "数据浏览", desc: "浏览企业数据源", to: "/admin/bi/data", icon: Database },
    { label: "可观测仪表盘", desc: "系统健康度概览", to: "/admin/monitor/dashboard", icon: Gauge },
  ];

  const activity = metrics?.activity ?? [];
  const maxSessions = Math.max(...activity.map((a) => a.sessions), 1);

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">仪表盘</h1>
            <p className="mt-1 text-sm text-secondary">系统概览与关键指标</p>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1 rounded-md bg-subtle p-0.5">
              {(Object.keys(RANGE_LABELS) as TimeRange[]).map((r) => (
                <button
                  key={r}
                  onClick={() => setTimeRange(r)}
                  className={cn(
                    "rounded-sm px-2.5 py-1 text-xs font-medium transition-colors",
                    timeRange === r
                      ? "bg-elevated text-foreground shadow-sm"
                      : "text-secondary hover:text-foreground",
                  )}
                >
                  {RANGE_LABELS[r]}
                </button>
              ))}
            </div>
            <Button variant="outline" size="sm" onClick={handleExport}>
              <Download className="h-3.5 w-3.5" />
              导出
            </Button>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6">
        {/* Stats Grid */}
        <div className="grid grid-cols-4 gap-4">
          {stats.map((stat) => (
            <div
              key={stat.label}
              className="rounded-md border border-border bg-elevated p-4 shadow-sm"
            >
              <div className="flex items-center gap-2 text-tertiary">
                <stat.icon className="h-4 w-4" strokeWidth={1.75} />
                <span className="text-xs font-medium">{stat.label}</span>
              </div>
              <p className="mt-2 text-2xl font-semibold text-foreground">
                {stat.value}
              </p>
            </div>
          ))}
        </div>

        {/* Activity Chart + Metrics */}
        <div className="mt-6 grid grid-cols-3 gap-6">
          {/* Activity Chart */}
          <div className="col-span-2 rounded-md border border-border bg-elevated p-4 shadow-sm">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="text-sm font-medium text-foreground">
                会话活动趋势
              </h2>
              <span className="text-xs text-tertiary">
                {RANGE_LABELS[timeRange]} · {metrics?.granularity ?? "day"}
              </span>
            </div>
            {activity.length === 0 ? (
              <div className="flex h-32 items-center justify-center text-xs text-tertiary">
                暂无活动数据
              </div>
            ) : (
              <div className="flex h-32 items-end gap-1">
                {activity.map((a) => (
                  <div
                    key={a.bucket}
                    className="group relative flex-1 rounded-t bg-accent/60 transition-colors hover:bg-accent"
                    style={{ height: `${(a.sessions / maxSessions) * 100}%`, minHeight: "4px" }}
                    title={`${a.bucket}: ${a.sessions} 会话`}
                  >
                    <span className="absolute -top-5 left-1/2 -translate-x-1/2 text-xs text-foreground opacity-0 group-hover:opacity-100">
                      {a.sessions}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Metrics Counts */}
          {metrics && (
            <div className="rounded-md border border-border bg-elevated p-4 shadow-sm">
              <h2 className="mb-3 text-sm font-medium text-foreground">
                系统指标
              </h2>
              <div className="space-y-2">
                {Object.entries(metrics.counts).map(([key, value]) => (
                  <div key={key} className="flex items-center justify-between">
                    <span className="text-xs text-tertiary">{key}</span>
                    <span className="text-sm font-semibold text-foreground">{value}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Pending Approvals + Recent Audit Logs */}
        <div className="mt-6 grid grid-cols-2 gap-6">
          {/* Pending Approvals */}
          <div className="rounded-md border border-border bg-elevated p-4 shadow-sm">
            <h2 className="mb-3 text-sm font-medium text-foreground">
              待审批申请
            </h2>
            {pendingApprovals.length === 0 ? (
              <p className="py-4 text-center text-xs text-tertiary">
                暂无待审批
              </p>
            ) : (
              <div className="space-y-2">
                {pendingApprovals.slice(0, 5).map((a) => (
                  <div
                    key={a.id}
                    className="flex items-center justify-between rounded bg-subtle/50 px-3 py-2"
                  >
                    <span className="text-xs text-foreground">{a.reason}</span>
                    <span className="text-xs text-tertiary">
                      {relativeTime(a.created_at)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Recent Audit Logs */}
          <div className="rounded-md border border-border bg-elevated p-4 shadow-sm">
            <h2 className="mb-3 text-sm font-medium text-foreground">
              最近审计
            </h2>
            {auditLogs.length === 0 ? (
              <p className="py-4 text-center text-xs text-tertiary">
                暂无审计记录
              </p>
            ) : (
              <div className="space-y-2">
                {auditLogs.slice(0, 5).map((log) => (
                  <div
                    key={log.id}
                    className="flex items-center justify-between rounded bg-subtle/50 px-3 py-2"
                  >
                    <span className="text-xs text-foreground">
                      {log.action} · {log.resource_type}
                    </span>
                    <span className="text-xs text-tertiary">
                      {relativeTime(log.created_at)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Quick Shortcuts */}
        <div className="mt-6">
          <h2 className="mb-3 text-sm font-medium text-foreground">快捷入口</h2>
          <div className="grid grid-cols-3 gap-4">
            {shortcuts.map((s) => (
              <Link
                key={s.to}
                to={s.to}
                className="group flex items-center gap-3 rounded-md border border-border bg-elevated p-4 shadow-sm transition-all hover:border-accent/30 hover:shadow-md"
              >
                <div className="flex h-10 w-10 items-center justify-center rounded-md bg-accent/10 text-accent">
                  <s.icon className="h-5 w-5" strokeWidth={1.75} />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-foreground">{s.label}</p>
                  <p className="truncate text-xs text-tertiary">{s.desc}</p>
                </div>
                <ArrowRight className="h-4 w-4 text-tertiary opacity-0 transition-opacity group-hover:opacity-100" />
              </Link>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
