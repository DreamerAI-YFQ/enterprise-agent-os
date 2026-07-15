import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import { Spinner, Badge, cn } from "@eaos/shared";
import { relativeTime } from "../lib/relative-time";
import {
  Workflow,
  History,
  ChevronRight,
  Layers,
  Clock,
  CheckCircle2,
  XCircle,
  Settings,
} from "lucide-react";

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

type Tab = "history" | "config" | "templates";

export default function WorkflowsPage() {
  const [tab, setTab] = useState<Tab>("history");

  const tasksQuery = useQuery({
    queryKey: ["tasks", "completed"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/tasks", {
        params: { query: { status: "completed" } },
      });
      if (error || !data) return [] as Task[];
      return data as unknown as Task[];
    },
  });

  const tasks = tasksQuery.data ?? [];

  const tabs: { value: Tab; label: string; icon: typeof History }[] = [
    { value: "history", label: "执行历史", icon: History },
    { value: "config", label: "协作模式", icon: Settings },
    { value: "templates", label: "子任务模板", icon: Layers },
  ];

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <h1 className="text-2xl font-semibold text-foreground">工作流管理</h1>
        <p className="mt-1 text-sm text-secondary">
          查看 Agent 执行历史、配置协作模式与子任务模板
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
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6">
        {tab === "history" ? (
          tasksQuery.isLoading ? (
            <div className="flex h-40 items-center justify-center">
              <Spinner />
            </div>
          ) : tasks.length === 0 ? (
            <div className="flex h-full items-center justify-center">
              <div className="flex flex-col items-center gap-3 text-center">
                <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                  <History className="h-8 w-8" strokeWidth={1.5} />
                </div>
                <h3 className="text-lg font-medium text-foreground">暂无执行历史</h3>
                <p className="max-w-sm text-sm text-secondary">
                  Agent 完成任务后，执行记录会显示在这里
                </p>
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              {tasks.map((task) => (
                <div
                  key={task.id}
                  className="group flex items-center gap-3 rounded-md border border-border bg-elevated p-4 transition-colors hover:bg-subtle/30"
                >
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-accent-subtle/30">
                    <Workflow className="h-5 w-5 text-accent" strokeWidth={1.5} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium text-foreground">
                        {task.title || `任务 ${task.session_id.slice(0, 8)}`}
                      </span>
                      <Badge
                        variant="outline"
                        className={cn(
                          "text-xs",
                          task.status === "completed" && "border-success/30 text-success",
                          task.status === "failed" && "border-danger/30 text-danger"
                        )}
                      >
                        {task.status === "completed" ? (
                          <CheckCircle2 className="mr-1 h-3 w-3" />
                        ) : (
                          <XCircle className="mr-1 h-3 w-3" />
                        )}
                        {task.status}
                      </Badge>
                    </div>
                    <div className="mt-1 flex items-center gap-3 text-xs text-tertiary">
                      <span>Agent: {task.agent_name || task.agent_id.slice(0, 8)}</span>
                      <span>·</span>
                      <span className="flex items-center gap-1">
                        <Clock className="h-3 w-3" />
                        {relativeTime(task.created_at)}
                      </span>
                    </div>
                  </div>
                  <a
                    href={`#/admin/traces?trace_id=${task.session_id}`}
                    className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-accent opacity-0 transition-opacity hover:bg-accent-subtle group-hover:opacity-100"
                  >
                    查看链路 <ChevronRight className="h-3 w-3" />
                  </a>
                </div>
              ))}
            </div>
          )
        ) : tab === "config" ? (
          <div className="flex h-full items-center justify-center">
            <div className="flex flex-col items-center gap-3 text-center">
              <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                <Settings className="h-8 w-8" strokeWidth={1.5} />
              </div>
              <h3 className="text-lg font-medium text-foreground">协作模式配置</h3>
              <p className="max-w-md text-sm text-secondary">
                多 Agent 协作模式（串行/并行/路由）配置功能需要后端工作流引擎支持。
                当前 Agent 通过 /invoke 接口直接执行单任务。
              </p>
              <div className="mt-4 rounded-md border border-border bg-subtle/30 p-4 text-left">
                <p className="text-xs font-medium text-secondary">规划中支持的协作模式：</p>
                <ul className="mt-2 space-y-1 text-xs text-tertiary">
                  <li>· 串行流水线：Agent A → Agent B → Agent C</li>
                  <li>· 并行分发：一个任务分发到多个 Agent 同时执行</li>
                  <li>· 路由选择：根据任务类型自动路由到对应 Agent</li>
                  <li>· 编排者模式：主 Agent 调度子 Agent 完成复杂任务</li>
                </ul>
              </div>
            </div>
          </div>
        ) : (
          <div className="flex h-full items-center justify-center">
            <div className="flex flex-col items-center gap-3 text-center">
              <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                <Layers className="h-8 w-8" strokeWidth={1.5} />
              </div>
              <h3 className="text-lg font-medium text-foreground">子任务模板</h3>
              <p className="max-w-md text-sm text-secondary">
                预定义子任务模板（如"数据收集"、"分析报告"、"发送邮件"）可标准化 Agent 执行流程。
                此功能需要后端工作流模板引擎支持，即将上线。
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
