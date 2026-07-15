import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import { Spinner } from "@eaos/shared";
import { TaskTabs, type TaskTab } from "../components/tasks/task-tabs";
import { TaskCard, type Task } from "../components/tasks/task-card";
import {
  TaskFilters,
  type TaskTypeFilter,
  type TaskSortBy,
} from "../components/tasks/task-filters";
import { TaskEmptyState } from "../components/tasks/task-empty-state";

const TAB_HEADERS: Record<TaskTab, { title: string; subtitle: string }> = {
  pending: {
    title: "任务中心",
    subtitle: "待审批的高风险操作需要你的关注",
  },
  running: {
    title: "任务中心",
    subtitle: "进行中的会话和 Agent 执行",
  },
  completed: {
    title: "任务中心",
    subtitle: "已完成的会话历史",
  },
};

export default function TasksPage() {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState<TaskTab>("pending");
  const [typeFilter, setTypeFilter] = useState<TaskTypeFilter>("all");
  const [sortBy, setSortBy] = useState<TaskSortBy>("updated");

  // Fetch tasks for the active tab.
  // Pending + running auto-refresh (status may change); completed is static.
  const tasksQuery = useQuery({
    queryKey: ["tasks", activeTab],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/tasks", {
        params: { query: { status: activeTab } },
      });
      if (error || !data) return [] as Task[];
      return data as unknown as Task[];
    },
    refetchInterval:
      activeTab === "pending" || activeTab === "running" ? 30_000 : false,
  });

  // Pending count for the tab badge (always fetched, regardless of active tab).
  const pendingCountQuery = useQuery({
    queryKey: ["tasks", "pending", "count"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/tasks", {
        params: { query: { status: "pending" } },
      });
      if (error || !data) return 0;
      return (data as unknown as Task[]).length;
    },
    refetchInterval: 30_000,
  });

  const counts: Record<TaskTab, number | undefined> = {
    pending: pendingCountQuery.data,
    running: undefined,
    completed: undefined,
  };

  // Apply client-side type filter + sort.
  const filteredTasks = useMemo(() => {
    let list = tasksQuery.data ?? [];
    if (typeFilter !== "all") {
      list = list.filter((t) => t.type === typeFilter);
    }
    const sortKey = sortBy === "updated" ? "updated_at" : "created_at";
    return [...list].sort((a, b) => {
      const av = a[sortKey] ?? "";
      const bv = b[sortKey] ?? "";
      return bv.localeCompare(av);
    });
  }, [tasksQuery.data, typeFilter, sortBy]);

  const handleTaskClick = (task: Task) => {
    if (task.session_id) {
      navigate(`/app?session=${task.session_id}`);
    }
  };

  const isLoading = tasksQuery.isLoading;
  const tasks = filteredTasks;
  const header = TAB_HEADERS[activeTab];

  return (
    <div className="flex h-full flex-col">
      {/* Page Header */}
      <div className="border-b border-border-subtle px-8 py-6">
        <h1 className="text-2xl font-semibold text-foreground">{header.title}</h1>
        <p className="mt-1 text-sm text-secondary">{header.subtitle}</p>
      </div>

      {/* Tabs */}
      <div className="border-b border-border-subtle px-8 py-3">
        <TaskTabs active={activeTab} counts={counts} onChange={setActiveTab} />
      </div>

      {/* Filters */}
      <div className="flex items-center justify-between px-8 py-3">
        <TaskFilters
          typeFilter={typeFilter}
          sortBy={sortBy}
          onTypeChange={setTypeFilter}
          onSortChange={setSortBy}
        />
      </div>

      {/* Task List */}
      <div className="flex-1 overflow-y-auto px-8 pb-8">
        {isLoading ? (
          <div className="flex h-40 items-center justify-center">
            <Spinner />
          </div>
        ) : tasks.length === 0 ? (
          <TaskEmptyState tab={activeTab} />
        ) : (
          <div className="space-y-3">
            {tasks.map((task) => (
              <TaskCard
                key={task.id}
                task={task}
                onClick={handleTaskClick}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
