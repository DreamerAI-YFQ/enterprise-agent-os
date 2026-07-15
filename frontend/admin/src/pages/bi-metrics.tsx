import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import { Spinner } from "@eaos/shared";
import {
  Users,
  Bot,
  MessageSquare,
  Sparkles,
  FileText,
  ShieldCheck,
  Bell,
  TrendingUp,
  AlertTriangle,
} from "lucide-react";

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

const DEFAULT_THRESHOLDS: Record<string, number> = {
  sessions: 100,
  pending_approvals: 10,
  unread_notifications: 50,
};

const STORAGE_KEY = "eaos:metric-thresholds";

function loadThresholds(): Record<string, number> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_THRESHOLDS;
    return { ...DEFAULT_THRESHOLDS, ...JSON.parse(raw) };
  } catch {
    return DEFAULT_THRESHOLDS;
  }
}

export default function BiMetricsPage() {
  const [thresholds, setThresholds] = useState(loadThresholds);
  const [editingThreshold, setEditingThreshold] = useState(false);

  const metricsQuery = useQuery({
    queryKey: ["admin", "metrics"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/metrics", {});
      if (error || !data) return null;
      return data as unknown as MetricsData;
    },
    refetchInterval: 30_000,
  });

  const saveThresholds = (newThresholds: Record<string, number>) => {
    setThresholds(newThresholds);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(newThresholds));
    } catch {
      // ignore
    }
  };

  if (metricsQuery.isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  }

  const metrics = metricsQuery.data;
  const counts = metrics?.counts;
  const activity = metrics?.activity_7d ?? [];

  const maxSessions = Math.max(...activity.map((a) => a.sessions), 1);

  const statCards = [
    { key: "users", label: "用户", value: counts?.users ?? 0, icon: Users, threshold: null },
    { key: "agents", label: "Agent", value: counts?.agents ?? 0, icon: Bot, threshold: null },
    { key: "sessions", label: "会话", value: counts?.sessions ?? 0, icon: MessageSquare, threshold: thresholds.sessions },
    { key: "skills", label: "技能", value: counts?.skills ?? 0, icon: Sparkles, threshold: null },
    { key: "documents", label: "文档", value: counts?.documents ?? 0, icon: FileText, threshold: null },
    { key: "pending_approvals", label: "待审批", value: counts?.pending_approvals ?? 0, icon: ShieldCheck, threshold: thresholds.pending_approvals },
    { key: "unread_notifications", label: "未读通知", value: counts?.unread_notifications ?? 0, icon: Bell, threshold: thresholds.unread_notifications },
  ];

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">指标中心</h1>
            <p className="mt-1 text-sm text-secondary">
              系统关键指标与 7 天活动趋势
            </p>
          </div>
          <button
            onClick={() => setEditingThreshold(!editingThreshold)}
            className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-secondary transition-colors hover:bg-subtle"
          >
            {editingThreshold ? "完成" : "阈值配置"}
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6">
        {/* Stats Grid */}
        <div className="grid grid-cols-4 gap-4">
          {statCards.map((stat) => {
            const isOverThreshold =
              stat.threshold !== null && stat.threshold !== undefined && stat.value > stat.threshold;
            return (
              <div
                key={stat.key}
                className={`rounded-md border bg-elevated p-4 shadow-sm transition-colors ${
                  isOverThreshold ? "border-danger/40" : "border-border"
                }`}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-tertiary">
                    <stat.icon className="h-4 w-4" strokeWidth={1.75} />
                    <span className="text-xs font-medium">{stat.label}</span>
                  </div>
                  {isOverThreshold && (
                    <AlertTriangle className="h-3.5 w-3.5 text-danger" />
                  )}
                </div>
                <p
                  className={`mt-2 text-2xl font-semibold ${
                    isOverThreshold ? "text-danger" : "text-foreground"
                  }`}
                >
                  {stat.value}
                </p>
                {stat.threshold !== null && stat.threshold !== undefined && (
                  <p className="mt-0.5 text-xs text-tertiary">
                    阈值: {stat.threshold}
                    {editingThreshold && (
                      <input
                        type="number"
                        value={stat.threshold}
                        onChange={(e) => {
                          const val = parseInt(e.target.value) || 0;
                          saveThresholds({ ...thresholds, [stat.key]: val });
                        }}
                        className="ml-2 w-16 rounded border border-border bg-subtle px-1 py-0.5 text-xs"
                      />
                    )}
                  </p>
                )}
              </div>
            );
          })}
        </div>

        {/* 7-Day Activity Trend */}
        <div className="mt-6 rounded-md border border-border bg-elevated p-6 shadow-sm">
          <div className="mb-4 flex items-center gap-2">
            <TrendingUp className="h-4 w-4 text-accent" />
            <h2 className="text-sm font-medium text-foreground">7 天会话活动趋势</h2>
          </div>
          {activity.length === 0 ? (
            <p className="py-8 text-center text-xs text-tertiary">暂无活动数据</p>
          ) : (
            <div className="flex h-48 items-end gap-2">
              {activity.map((day, idx) => {
                const height = (day.sessions / maxSessions) * 100;
                const date = new Date(day.day);
                const label = `${date.getMonth() + 1}/${date.getDate()}`;
                return (
                  <div
                    key={idx}
                    className="group flex flex-1 flex-col items-center gap-2"
                  >
                    <span className="text-xs text-tertiary opacity-0 transition-opacity group-hover:opacity-100">
                      {day.sessions}
                    </span>
                    <div className="flex w-full flex-1 items-end">
                      <div
                        className="w-full rounded-t bg-accent/70 transition-all duration-base ease-out hover:bg-accent"
                        style={{ height: `${Math.max(height, 2)}%` }}
                      />
                    </div>
                    <span className="text-xs text-tertiary">{label}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Definitions */}
        <div className="mt-6 rounded-md border border-border bg-elevated p-6 shadow-sm">
          <h2 className="mb-3 text-sm font-medium text-foreground">指标定义</h2>
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-secondary">用户</span>
              <span className="text-tertiary">租户内注册用户总数</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-secondary">Agent</span>
              <span className="text-tertiary">已配置的 Agent 数量（含停用）</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-secondary">会话</span>
              <span className="text-tertiary">用户与 Agent 的对话会话总数</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-secondary">待审批</span>
              <span className="text-tertiary">等待管理员审批的 HITL 请求</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-secondary">活动趋势</span>
              <span className="text-tertiary">最近 7 天每日会话创建数</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
