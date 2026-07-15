import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import { Button, Spinner, cn, toast } from "@eaos/shared";
import { Bot, Plus, X, Settings2 } from "lucide-react";

interface Agent {
  id: string;
  scope: string;
  owner_id: string | null;
  name: string;
  description: string;
  model_config: Record<string, unknown>;
  capability: {
    allowed_models: string[];
    allowed_datasources: string[];
    writable_datasources: string[];
    allowed_skill_categories: string[];
    max_task_duration_sec: number;
    max_iterations: number;
  };
  assigned_skills: string[];
  status: string;
}

const SCOPE_LABELS: Record<string, string> = {
  personal: "个人",
  department: "部门",
  company: "公司",
};

export default function AgentsPage() {
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [editingAgent, setEditingAgent] = useState<Agent | null>(null);

  const query = useQuery({
    queryKey: ["agents"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/agents", {});
      if (error || !data) return [] as Agent[];
      return data as unknown as Agent[];
    },
  });

  const agents = query.data ?? [];

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">Agent 管理</h1>
            <p className="mt-1 text-sm text-secondary">
              管理 Agent 配置和能力边界
            </p>
          </div>
          <Button onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4" />
            创建 Agent
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-8 pb-8">
        {query.isLoading ? (
          <div className="flex h-40 items-center justify-center">
            <Spinner />
          </div>
        ) : agents.length === 0 ? (
          <div className="flex h-full items-center justify-center p-8">
            <div className="flex flex-col items-center gap-3 text-center">
              <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                <Bot className="h-8 w-8" strokeWidth={1.5} />
              </div>
              <h3 className="text-2xl font-semibold text-foreground">
                暂无 Agent
              </h3>
              <p className="max-w-sm text-sm text-secondary">
                创建一个 Agent，定义其能力和权限边界
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            {agents.map((agent) => (
              <div
                key={agent.id}
                className="rounded-md border border-border bg-elevated p-4 shadow-sm"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-foreground">
                        {agent.name}
                      </span>
                      <span className="rounded-full bg-subtle px-2 py-0.5 text-xs text-tertiary">
                        {SCOPE_LABELS[agent.scope] ?? agent.scope}
                      </span>
                      <span
                        className={cn(
                          "rounded-full px-2 py-0.5 text-xs",
                          agent.status === "active"
                            ? "bg-success/10 text-success"
                            : "bg-tertiary/10 text-tertiary"
                        )}
                      >
                        {agent.status === "active" ? "运行中" : "已停止"}
                      </span>
                    </div>
                    {agent.description && (
                      <p className="mt-1 text-sm text-secondary line-clamp-1">
                        {agent.description}
                      </p>
                    )}
                    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-tertiary">
                      <span>模型: {agent.model_config?.model as string ?? "未配置"}</span>
                      <span>·</span>
                      <span>技能数: {agent.assigned_skills.length}</span>
                      <span>·</span>
                      <span>最大迭代: {agent.capability.max_iterations}</span>
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setEditingAgent(agent)}
                  >
                    <Settings2 className="h-3.5 w-3.5" />
                    配置
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {showCreate && (
        <CreateAgentModal
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            setShowCreate(false);
            void queryClient.invalidateQueries({ queryKey: ["agents"] });
          }}
        />
      )}

      {editingAgent && (
        <EditCapabilityModal
          agent={editingAgent}
          onClose={() => setEditingAgent(null)}
          onSaved={() => {
            setEditingAgent(null);
            void queryClient.invalidateQueries({ queryKey: ["agents"] });
          }}
        />
      )}
    </div>
  );
}

function CreateAgentModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [scope, setScope] = useState("personal");
  const [description, setDescription] = useState("");

  const createMutation = useMutation({
    mutationFn: async () => {
      await apiClient.POST("/admin/agents", {
        body: {
          name,
          scope,
          description: description || undefined,
          model_settings: {},
        },
      });
    },
    onSuccess: () => {
      toast.show({ title: "Agent 已创建", variant: "success" });
      onCreated();
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-lg bg-elevated p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-foreground">创建 Agent</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-tertiary hover:bg-subtle hover:text-foreground"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            createMutation.mutate();
          }}
          className="space-y-3"
        >
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary">
              名称
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-accent focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary">
              作用域
            </label>
            <select
              value={scope}
              onChange={(e) => setScope(e.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none"
            >
              <option value="personal">个人</option>
              <option value="department">部门</option>
              <option value="company">公司</option>
            </select>
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

function EditCapabilityModal({
  agent,
  onClose,
  onSaved,
}: {
  agent: Agent;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [maxIterations, setMaxIterations] = useState(
    agent.capability.max_iterations
  );
  const [maxDuration, setMaxDuration] = useState(
    agent.capability.max_task_duration_sec
  );

  const updateMutation = useMutation({
    mutationFn: async () => {
      await apiClient.PUT("/admin/agents/{agent_id}", {
        params: { path: { agent_id: agent.id } },
        body: {
          max_iterations: maxIterations,
          max_task_duration_sec: maxDuration,
        },
      });
    },
    onSuccess: () => {
      toast.show({ title: "配置已更新", variant: "success" });
      onSaved();
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-lg bg-elevated p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-foreground">
            配置 {agent.name}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-tertiary hover:bg-subtle hover:text-foreground"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            updateMutation.mutate();
          }}
          className="space-y-3"
        >
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary">
              最大迭代次数
            </label>
            <input
              type="number"
              value={maxIterations}
              onChange={(e) => setMaxIterations(Number(e.target.value))}
              min={1}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-accent focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary">
              最大执行时长（秒）
            </label>
            <input
              type="number"
              value={maxDuration}
              onChange={(e) => setMaxDuration(Number(e.target.value))}
              min={1}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-accent focus:outline-none"
            />
          </div>
          <div className="rounded-md bg-subtle/50 p-3">
            <p className="text-xs text-tertiary">允许的模型</p>
            <div className="mt-1 flex flex-wrap gap-1">
              {agent.capability.allowed_models.map((m) => (
                <span
                  key={m}
                  className="rounded bg-elevated px-1.5 py-0.5 text-xs text-foreground"
                >
                  {m}
                </span>
              ))}
            </div>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" type="button" onClick={onClose}>
              取消
            </Button>
            <Button type="submit" disabled={updateMutation.isPending}>
              {updateMutation.isPending ? "保存中..." : "保存"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
