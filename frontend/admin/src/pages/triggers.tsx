import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import {
  Button,
  Input,
  Spinner,
  Badge,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogClose,
  toast,
} from "@eaos/shared";
import { Zap, Plus, Trash2, Clock, Bell, Bot } from "lucide-react";

interface Trigger {
  id: string;
  agent_id: string;
  trigger_type: string;
  condition: Record<string, unknown>;
  notify_channel: string;
  interval_sec: number;
  created_at?: string;
  last_fired?: string;
}

interface Agent {
  id: string;
  name: string;
  status: string;
}

const TRIGGER_TYPES = [
  { value: "schedule", label: "定时调度" },
  { value: "event", label: "事件触发" },
  { value: "webhook", label: "Webhook" },
  { value: "threshold", label: "阈值触发" },
];

const NOTIFY_CHANNELS = [
  { value: "email", label: "邮件" },
  { value: "slack", label: "Slack" },
  { value: "webhook", label: "Webhook" },
  { value: "none", label: "无通知" },
];

export default function TriggersPage() {
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({
    agent_id: "",
    trigger_type: "schedule",
    condition_json: '{"type": "cron", "expression": "0 9 * * *"}',
    notify_channel: "email",
    interval_sec: "300",
  });

  const triggersQuery = useQuery({
    queryKey: ["admin", "triggers"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/triggers", {});
      if (error || !data) return [] as Trigger[];
      return data as unknown as Trigger[];
    },
  });

  const agentsQuery = useQuery({
    queryKey: ["agents"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/agents", {});
      if (error || !data) return [] as Agent[];
      return data as unknown as Agent[];
    },
  });

  const createMutation = useMutation({
    mutationFn: async (input: {
      agent_id: string;
      trigger_type: string;
      condition: Record<string, unknown>;
      notify_channel: string;
      interval_sec: number;
    }) => {
      const { error } = await apiClient.POST("/admin/triggers", {
        body: input as never,
      });
      if (error) throw new Error("创建失败");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "triggers"] });
      toast.show({ title: "创建成功", description: "触发器已添加", variant: "success" });
      setShowCreate(false);
      setForm({
        agent_id: "",
        trigger_type: "schedule",
        condition_json: '{"type": "cron", "expression": "0 9 * * *"}',
        notify_channel: "email",
        interval_sec: "300",
      });
    },
    onError: () => {
      toast.show({ title: "创建失败", description: "请检查参数", variant: "danger" });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      const { error } = await apiClient.DELETE("/admin/triggers/{trigger_id}", {
        params: { path: { trigger_id: id } },
      });
      if (error) throw new Error("删除失败");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "triggers"] });
      toast.show({ title: "已删除", description: "触发器已移除", variant: "default" });
    },
    onError: () => {
      toast.show({ title: "删除失败", variant: "danger" });
    },
  });

  const handleCreate = () => {
    if (!form.agent_id) {
      toast.show({ title: "请选择 Agent", variant: "danger" });
      return;
    }
    let condition: Record<string, unknown> = {};
    try {
      condition = JSON.parse(form.condition_json);
    } catch {
      toast.show({ title: "JSON 格式错误", description: "请检查 condition 格式", variant: "danger" });
      return;
    }
    createMutation.mutate({
      agent_id: form.agent_id,
      trigger_type: form.trigger_type,
      condition,
      notify_channel: form.notify_channel,
      interval_sec: parseInt(form.interval_sec, 10) || 300,
    });
  };

  const triggers = triggersQuery.data ?? [];
  const agents = agentsQuery.data ?? [];
  const agentMap = new Map(agents.map((a) => [a.id, a]));

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">调度管理</h1>
            <p className="mt-1 text-sm text-secondary">
              环境触发器 — 定时调度、事件触发、阈值监控
            </p>
          </div>
          <Button onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4" />
            添加触发器
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6">
        {triggersQuery.isLoading ? (
          <div className="flex h-40 items-center justify-center"><Spinner /></div>
        ) : triggers.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <div className="flex flex-col items-center gap-3 text-center">
              <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                <Zap className="h-8 w-8" strokeWidth={1.5} />
              </div>
              <h3 className="text-lg font-medium text-foreground">暂无触发器</h3>
              <p className="max-w-sm text-sm text-secondary">
                创建触发器以自动执行 Agent 任务（定时调度、事件触发、阈值监控）
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            {triggers.map((t) => {
              const agent = agentMap.get(t.agent_id);
              const typeLabel = TRIGGER_TYPES.find((x) => x.value === t.trigger_type)?.label ?? t.trigger_type;
              const channelLabel = NOTIFY_CHANNELS.find((x) => x.value === t.notify_channel)?.label ?? t.notify_channel;
              return (
                <div key={t.id} className="rounded-md border border-border bg-elevated p-4 shadow-sm">
                  <div className="flex items-start justify-between">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <Zap className="h-4 w-4 text-accent" />
                        <Badge variant="secondary" className="text-xs">{typeLabel}</Badge>
                        {agent && (
                          <span className="flex items-center gap-1 text-xs text-secondary">
                            <Bot className="h-3 w-3" /> {agent.name}
                          </span>
                        )}
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-4 text-xs text-tertiary">
                        <span className="flex items-center gap-1">
                          <Clock className="h-3 w-3" /> 间隔 {t.interval_sec}s
                        </span>
                        <span className="flex items-center gap-1">
                          <Bell className="h-3 w-3" /> {channelLabel}
                        </span>
                        {t.last_fired && (
                          <span>上次触发: {new Date(t.last_fired).toLocaleString("zh-CN")}</span>
                        )}
                      </div>
                      {Object.keys(t.condition ?? {}).length > 0 && (
                        <pre className="mt-2 overflow-x-auto rounded bg-subtle/40 p-2 text-xs text-tertiary">
                          {JSON.stringify(t.condition, null, 2)}
                        </pre>
                      )}
                    </div>
                    <button
                      onClick={() => deleteMutation.mutate(t.id)}
                      className="shrink-0 rounded-md border border-border p-1.5 text-secondary transition-colors hover:bg-subtle hover:text-danger"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Create Dialog */}
      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>添加触发器</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div>
              <label className="text-xs text-tertiary">目标 Agent</label>
              <select
                value={form.agent_id}
                onChange={(e) => setForm({ ...form, agent_id: e.target.value })}
                className="mt-1 w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm text-foreground"
              >
                <option value="">选择 Agent...</option>
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-xs text-tertiary">触发类型</label>
              <select
                value={form.trigger_type}
                onChange={(e) => setForm({ ...form, trigger_type: e.target.value })}
                className="mt-1 w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm text-foreground"
              >
                {TRIGGER_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-xs text-tertiary">条件 (JSON)</label>
              <textarea
                value={form.condition_json}
                onChange={(e) => setForm({ ...form, condition_json: e.target.value })}
                className="mt-1 w-full rounded-md border border-border bg-transparent px-3 py-2 font-mono text-xs text-foreground"
                rows={4}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-tertiary">通知渠道</label>
                <select
                  value={form.notify_channel}
                  onChange={(e) => setForm({ ...form, notify_channel: e.target.value })}
                  className="mt-1 w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm text-foreground"
                >
                  {NOTIFY_CHANNELS.map((c) => (
                    <option key={c.value} value={c.value}>{c.label}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs text-tertiary">检查间隔 (秒)</label>
                <Input
                  type="number"
                  value={form.interval_sec}
                  onChange={(e) => setForm({ ...form, interval_sec: e.target.value })}
                  className="mt-1"
                />
              </div>
            </div>
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">取消</Button>
            </DialogClose>
            <Button onClick={handleCreate} disabled={createMutation.isPending}>
              {createMutation.isPending ? "创建中..." : "创建"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
