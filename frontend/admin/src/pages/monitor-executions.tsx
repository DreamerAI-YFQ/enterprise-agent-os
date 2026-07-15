import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import { Spinner, Badge, cn } from "@eaos/shared";
import { relativeTime } from "../lib/relative-time";
import { Activity, RefreshCw, Filter, Clock, DollarSign, Bot, ChevronRight } from "lucide-react";

interface Task {
  id: string;
  session_id: string;
  agent_id: string;
  agent_name?: string;
  status: string;
  title: string;
  created_at: string;
  updated_at: string;
}

interface SpanOverview {
  tenant_id: string;
  total_agents: number;
  active_users_today: number;
  total_tokens_today: number;
  total_cost_usd_today: number;
  top_skills: { name: string; count: number }[];
  task_success_rate: number;
}

type StatusFilter = "all" | "running" | "pending" | "completed";

export default function MonitorExecutionsPage() {
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("running");
  const [agentFilter] = useState("");

  const now = new Date();
  const start = new Date(now.getTime() - 24 * 60 * 60 * 1000);

  const tasksQuery = useQuery({
    queryKey: ["tasks", statusFilter],
    queryFn: async () => {
      const params: Record<string, unknown> = {};
      if (statusFilter !== "all") params.status = statusFilter;
      const { data, error } = await apiClient.GET("/tasks", {
        params: { query: params },
      });
      if (error || !data) return [] as Task[];
      return data as unknown as Task[];
    },
    refetchInterval: 10_000,
  });

  const overviewQuery = useQuery({
    queryKey: ["admin", "spans", "overview", start.toISOString(), now.toISOString()],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/spans/overview", {
        params: {
          query: {
            start: start.toISOString(),
            end: now.toISOString(),
          },
        },
      });
      if (error || !data) return null;
      return data as unknown as SpanOverview;
    },
    refetchInterval: 30_000,
  });

  const tasks = tasksQuery.data ?? [];
  const overview = overviewQuery.data;

  const filteredTasks = agentFilter
    ? tasks.filter((t) => t.agent_id === agentFilter || t.agent_name === agentFilter)
    : tasks;

  const statusOptions: { value: StatusFilter; label: string; color: string }[] = [
    { value: "running", label: "执行中", color: "text-accent" },
    { value: "pending", label: "待审批", color: "text-warning" },
    { value: "completed", label: "已完成", color: "text-success" },
    { value: "all", label: "全部", color: "text-secondary" },
  ];

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">执行监控</h1>
            <p className="mt-1 text-sm text-secondary">
              实时监控 Agent 执行状态 · 每 10 秒自动刷新
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs text-tertiary">
            <RefreshCw className={cn("h-3.5 w-3.5", tasksQuery.isFetching && "animate-spin")} />
            <span>{tasksQuery.isFetching ? "刷新中..." : "自动刷新"}</span>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6">
        {/* Overview Stats */}
        {overview && (
          <div className="mb-6 grid grid-cols-4 gap-4">
            <div className="rounded-md border border-border bg-elevated p-4 shadow-sm">
              <div className="flex items-center gap-2 text-tertiary">
                <Bot className="h-4 w-4" />
                <span className="text-xs font-medium">活跃 Agent</span>
              </div>
              <p className="mt-2 text-2xl font-semibold text-foreground">
                {overview.total_agents}
              </p>
            </div>
            <div className="rounded-md border border-border bg-elevated p-4 shadow-sm">
              <div className="flex items-center gap-2 text-tertiary">
                <Activity className="h-4 w-4" />
                <span className="text-xs font-medium">今日活跃用户</span>
              </div>
              <p className="mt-2 text-2xl font-semibold text-foreground">
                {overview.active_users_today}
              </p>
            </div>
            <div className="rounded-md border border-border bg-elevated p-4 shadow-sm">
              <div className="flex items-center gap-2 text-tertiary">
                <Clock className="h-4 w-4" />
                <span className="text-xs font-medium">今日 Token</span>
              </div>
              <p className="mt-2 text-2xl font-semibold text-foreground">
                {overview.total_tokens_today.toLocaleString()}
              </p>
            </div>
            <div className="rounded-md border border-border bg-elevated p-4 shadow-sm">
              <div className="flex items-center gap-2 text-tertiary">
                <DollarSign className="h-4 w-4" />
                <span className="text-xs font-medium">今日成本</span>
              </div>
              <p className="mt-2 text-2xl font-semibold text-foreground">
                ${overview.total_cost_usd_today.toFixed(4)}
              </p>
            </div>
          </div>
        )}

        {/* Filter Bar */}
        <div className="mb-4 flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <Filter className="h-3.5 w-3.5 text-tertiary" />
            <span className="text-xs text-secondary">状态:</span>
          </div>
          {statusOptions.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setStatusFilter(opt.value)}
              className={cn(
                "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                statusFilter === opt.value
                  ? "bg-accent text-white"
                  : "bg-subtle text-secondary hover:text-foreground"
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>

        {/* Task List */}
        {tasksQuery.isLoading ? (
          <div className="flex h-40 items-center justify-center">
            <Spinner />
          </div>
        ) : filteredTasks.length === 0 ? (
          <div className="flex h-40 items-center justify-center">
            <div className="flex flex-col items-center gap-3 text-center">
              <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                <Activity className="h-8 w-8" strokeWidth={1.5} />
              </div>
              <p className="text-sm text-secondary">
                {statusFilter === "running"
                  ? "当前没有执行中的任务"
                  : statusFilter === "pending"
                  ? "当前没有待审批任务"
                  : "暂无任务"}
              </p>
            </div>
          </div>
        ) : (
          <div className="overflow-hidden rounded-md border border-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-subtle/50">
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-secondary">
                    任务
                  </th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-secondary">
                    Agent
                  </th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-secondary">
                    状态
                  </th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-secondary">
                    创建时间
                  </th>
                  <th className="px-4 py-2.5 text-left text-xs font-medium text-secondary">
                    更新时间
                  </th>
                  <th className="px-4 py-2.5"></th>
                </tr>
              </thead>
              <tbody>
                {filteredTasks.map((task) => (
                  <tr
                    key={task.id}
                    className="border-b border-border-subtle last:border-0 transition-colors hover:bg-subtle/30"
                  >
                    <td className="px-4 py-2.5">
                      <span className="text-sm text-foreground">
                        {task.title || task.session_id.slice(0, 12)}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="font-mono text-xs text-secondary">
                        {task.agent_name || task.agent_id.slice(0, 8)}
                      </span>
                    </td>
                    <td className="px-4 py-2.5">
                      <Badge
                        variant={
                          task.status === "running"
                            ? "default"
                            : task.status === "pending"
                            ? "secondary"
                            : "outline"
                        }
                        className={cn(
                          "text-xs",
                          task.status === "running" && "bg-accent/10 text-accent",
                          task.status === "pending" && "bg-warning/10 text-warning",
                          task.status === "completed" && "bg-success/10 text-success",
                          task.status === "failed" && "bg-danger/10 text-danger"
                        )}
                      >
                        {task.status}
                      </Badge>
                    </td>
                    <td className="px-4 py-2.5 text-xs text-tertiary">
                      {relativeTime(task.created_at)}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-tertiary">
                      {relativeTime(task.updated_at)}
                    </td>
                    <td className="px-4 py-2.5">
                      <button
                        onClick={() => {
                          window.location.hash = `/admin/traces?trace_id=${task.session_id}`;
                        }}
                        className="flex items-center gap-1 text-xs text-accent hover:underline"
                      >
                        链路 <ChevronRight className="h-3 w-3" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
