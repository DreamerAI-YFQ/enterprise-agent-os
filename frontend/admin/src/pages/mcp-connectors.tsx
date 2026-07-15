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
  cn,
  toast,
} from "@eaos/shared";
import { Plug, Plus, Trash2, CheckCircle2, XCircle, RefreshCw, Server, FileClock, AlertTriangle } from "lucide-react";

interface Connection {
  id: string;
  name: string;
  type: string;
  config: Record<string, unknown>;
  health_status: string;
  last_health_check: string | null;
  created_at?: string;
}

interface CallLog {
  id: string;
  tool_name: string;
  resource: string;
  operation: string;
  success: boolean;
  error: string | null;
  rolled_back: boolean;
  created_at: string;
}

interface McpConnector {
  id: string;
  name: string;
  type: string;
  status: string;
  tools?: string[];
}

const TYPE_LABELS: Record<string, string> = {
  mcp_stdio: "MCP (stdio)",
  mcp_sse: "MCP (SSE)",
  mcp_http: "MCP (HTTP)",
  http_api: "HTTP API",
};

export default function McpConnectorsPage() {
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [form, setForm] = useState({ name: "", type: "mcp_stdio", config_json: "", credentials_json: "" });
  const [callLogsConn, setCallLogsConn] = useState<Connection | null>(null);

  const connectionsQuery = useQuery({
    queryKey: ["admin", "connections"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/connections", {});
      if (error || !data) return [] as Connection[];
      return data as unknown as Connection[];
    },
  });

  const mcpQuery = useQuery({
    queryKey: ["admin", "mcp", "connectors"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/mcp/connectors", {});
      if (error || !data) return [] as McpConnector[];
      return data as unknown as McpConnector[];
    },
  });

  const createMutation = useMutation({
    mutationFn: async (input: { name: string; type: string; config: Record<string, unknown>; credentials?: Record<string, unknown> }) => {
      const body: Record<string, unknown> = { name: input.name, type: input.type, config: input.config };
      if (input.credentials && Object.keys(input.credentials).length > 0) {
        body.credentials = input.credentials;
      }
      const { error } = await apiClient.POST("/admin/connections", { body: body as never });
      if (error) throw new Error("创建失败");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "connections"] });
      toast.show({ title: "创建成功", description: "连接已注册", variant: "success" });
      setShowCreate(false);
      setForm({ name: "", type: "mcp_stdio", config_json: "", credentials_json: "" });
    },
    onError: () => {
      toast.show({ title: "创建失败", description: "请检查配置格式", variant: "danger" });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      const { error } = await apiClient.DELETE("/admin/connections/{conn_id}", {
        params: { path: { conn_id: id } },
      });
      if (error) throw new Error("删除失败");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "connections"] });
      toast.show({ title: "已删除", description: "连接已移除", variant: "default" });
    },
    onError: () => {
      toast.show({ title: "删除失败", variant: "danger" });
    },
  });

  const healthCheckMutation = useMutation({
    mutationFn: async (id: string) => {
      const { data, error } = await apiClient.POST("/admin/connections/{conn_id}/health-check", {
        params: { path: { conn_id: id } },
      });
      if (error) throw new Error("健康检查失败");
      return data as { status: string; error: string | null };
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["admin", "connections"] });
      const healthy = data?.status === "healthy";
      toast.show({
        title: healthy ? "连接正常" : "连接异常",
        description: data?.error ?? undefined,
        variant: healthy ? "success" : "danger",
      });
    },
    onError: () => {
      toast.show({ title: "健康检查失败", description: "连接可能不可用", variant: "danger" });
    },
  });

  const callLogsQuery = useQuery({
    queryKey: ["admin", "connections", callLogsConn?.id, "call-logs"],
    queryFn: async () => {
      if (!callLogsConn) return { items: [] as CallLog[], total: 0 };
      const { data, error } = await apiClient.GET("/admin/connections/{conn_id}/call-logs", {
        params: { path: { conn_id: callLogsConn.id }, query: { limit: 50, offset: 0 } },
      });
      if (error || !data) return { items: [] as CallLog[], total: 0 };
      return data as { items: CallLog[]; total: number };
    },
    enabled: callLogsConn !== null,
  });

  const handleCreate = () => {
    let config: Record<string, unknown> = {};
    let credentials: Record<string, unknown> = {};
    try {
      if (form.config_json) config = JSON.parse(form.config_json);
      if (form.credentials_json) credentials = JSON.parse(form.credentials_json);
    } catch {
      toast.show({ title: "JSON 格式错误", description: "请检查 config / credentials 是否为有效 JSON", variant: "danger" });
      return;
    }
    if (!form.name.trim()) {
      toast.show({ title: "请填写名称", variant: "danger" });
      return;
    }
    createMutation.mutate({ name: form.name, type: form.type, config, credentials });
  };

  const connections = connectionsQuery.data ?? [];
  const mcpConnectors = mcpQuery.data ?? [];

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">MCP & 连接器</h1>
            <p className="mt-1 text-sm text-secondary">
              外部系统连接管理（ERP/CRM/SaaS）+ MCP 标准协议连接器
            </p>
          </div>
          <Button onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4" />
            添加连接
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6">
        {/* External Connections */}
        <div className="mb-8">
          <div className="mb-4 flex items-center gap-2">
            <Server className="h-4 w-4 text-accent" />
            <h2 className="text-sm font-medium text-foreground">外部连接</h2>
            <Badge variant="outline" className="text-xs">{connections.length} 个</Badge>
          </div>

          {connectionsQuery.isLoading ? (
            <div className="flex h-20 items-center justify-center"><Spinner /></div>
          ) : connections.length === 0 ? (
            <div className="rounded-md border border-border-subtle bg-elevated p-8 text-center">
              <Plug className="mx-auto h-10 w-10 text-tertiary" strokeWidth={1.5} />
              <p className="mt-3 text-sm text-secondary">暂无外部连接</p>
              <p className="mt-1 text-xs text-tertiary">添加连接以集成 ERP/CRM/SaaS 系统</p>
            </div>
          ) : (
            <div className="space-y-3">
              {connections.map((conn) => (
                <div key={conn.id} className="rounded-md border border-border bg-elevated p-4 shadow-sm">
                  <div className="flex items-start justify-between">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <h3 className="text-sm font-semibold text-foreground">{conn.name}</h3>
                        <Badge variant="secondary" className="text-xs">
                          {TYPE_LABELS[conn.type] ?? conn.type}
                        </Badge>
                        {conn.health_status === "healthy" && (
                          <span className="flex items-center gap-1 text-xs text-success">
                            <CheckCircle2 className="h-3 w-3" /> 健康
                          </span>
                        )}
                        {conn.health_status === "unhealthy" && (
                          <span className="flex items-center gap-1 text-xs text-danger">
                            <XCircle className="h-3 w-3" /> 异常
                          </span>
                        )}
                        {conn.health_status === "unknown" && (
                          <span className="flex items-center gap-1 text-xs text-warning">
                            <AlertTriangle className="h-3 w-3" /> 未知
                          </span>
                        )}
                      </div>
                      <p className="mt-1 text-xs text-tertiary font-mono">{conn.id.slice(0, 8)}</p>
                      {conn.last_health_check && (
                        <p className="mt-0.5 text-xs text-tertiary">
                          上次检查: {new Date(conn.last_health_check).toLocaleString("zh-CN")}
                        </p>
                      )}
                      {Object.keys(conn.config ?? {}).length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1">
                          {Object.keys(conn.config).slice(0, 5).map((k) => (
                            <span key={k} className="rounded bg-subtle px-1.5 py-0.5 text-xs text-tertiary">
                              {k}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      <button
                        onClick={() => healthCheckMutation.mutate(conn.id)}
                        disabled={healthCheckMutation.isPending}
                        className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-secondary transition-colors hover:bg-subtle"
                        title="测试连接"
                      >
                        <RefreshCw className={cn("h-3 w-3", healthCheckMutation.isPending && "animate-spin")} />
                      </button>
                      <button
                        onClick={() => setCallLogsConn(conn)}
                        className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-secondary transition-colors hover:bg-subtle"
                        title="调用日志"
                      >
                        <FileClock className="h-3 w-3" />
                      </button>
                      <button
                        onClick={() => deleteMutation.mutate(conn.id)}
                        className="rounded-md border border-border px-2 py-1 text-xs text-secondary transition-colors hover:bg-subtle hover:text-danger"
                        title="删除"
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* MCP Connectors (read-only) */}
        <div>
          <div className="mb-4 flex items-center gap-2">
            <Plug className="h-4 w-4 text-accent" />
            <h2 className="text-sm font-medium text-foreground">MCP 连接器</h2>
            <Badge variant="outline" className="text-xs">{mcpConnectors.length} 个</Badge>
            <span className="text-xs text-tertiary">只读</span>
          </div>

          {mcpQuery.isLoading ? (
            <div className="flex h-20 items-center justify-center"><Spinner /></div>
          ) : mcpConnectors.length === 0 ? (
            <div className="rounded-md border border-border-subtle bg-elevated p-8 text-center">
              <p className="text-sm text-secondary">暂无 MCP 连接器</p>
              <p className="mt-1 text-xs text-tertiary">
                MCP 连接器由后端 Phase 7 工具执行层管理
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {mcpConnectors.map((mc) => (
                <div key={mc.id} className="rounded-md border border-border bg-elevated p-4 shadow-sm">
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="flex items-center gap-2">
                        <h3 className="text-sm font-semibold text-foreground">{mc.name}</h3>
                        <Badge variant="outline" className="text-xs">{mc.type}</Badge>
                        <Badge
                          variant="outline"
                          className={cn("text-xs", mc.status === "active" && "border-success/30 text-success")}
                        >
                          {mc.status}
                        </Badge>
                      </div>
                      {mc.tools && mc.tools.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1">
                          {mc.tools.slice(0, 8).map((t) => (
                            <span key={t} className="rounded bg-subtle px-1.5 py-0.5 text-xs text-tertiary">
                              {t}
                            </span>
                          ))}
                          {mc.tools.length > 8 && (
                            <span className="text-xs text-tertiary">+{mc.tools.length - 8}</span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Create Dialog */}
      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>添加外部连接</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div>
              <label className="text-xs text-tertiary">连接名称</label>
              <Input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="例: ACME ERP"
                className="mt-1"
              />
            </div>
            <div>
              <label className="text-xs text-tertiary">连接类型</label>
              <select
                value={form.type}
                onChange={(e) => setForm({ ...form, type: e.target.value })}
                className="mt-1 w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm text-foreground"
              >
                <option value="mcp_stdio">MCP (stdio)</option>
                <option value="mcp_sse">MCP (SSE)</option>
                <option value="mcp_http">MCP (HTTP)</option>
                <option value="http_api">HTTP API</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-tertiary">配置 (JSON)</label>
              <textarea
                value={form.config_json}
                onChange={(e) => setForm({ ...form, config_json: e.target.value })}
                placeholder='{"base_url": "https://erp.acme.com/api", "timeout": 30}'
                className="mt-1 w-full rounded-md border border-border bg-transparent px-3 py-2 font-mono text-xs text-foreground"
                rows={4}
              />
            </div>
            <div>
              <label className="text-xs text-tertiary">凭据 (JSON, 可选)</label>
              <textarea
                value={form.credentials_json}
                onChange={(e) => setForm({ ...form, credentials_json: e.target.value })}
                placeholder='{"api_key": "sk-...", "token": "..."}'
                className="mt-1 w-full rounded-md border border-border bg-transparent px-3 py-2 font-mono text-xs text-foreground"
                rows={3}
              />
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

      {/* Call Logs Dialog */}
      <Dialog open={callLogsConn !== null} onOpenChange={(open) => !open && setCallLogsConn(null)}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>调用日志 — {callLogsConn?.name}</DialogTitle>
          </DialogHeader>
          <div className="max-h-[60vh] overflow-y-auto">
            {callLogsQuery.isLoading ? (
              <div className="flex h-20 items-center justify-center"><Spinner /></div>
            ) : (callLogsQuery.data?.items ?? []).length === 0 ? (
              <div className="py-8 text-center">
                <FileClock className="mx-auto h-8 w-8 text-tertiary" strokeWidth={1.5} />
                <p className="mt-2 text-sm text-secondary">暂无调用记录</p>
                <p className="mt-1 text-xs text-tertiary">通过 WritePipeline 的写操作将记录在这里</p>
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-subtle/50 sticky top-0">
                  <tr className="text-left text-xs text-secondary">
                    <th className="px-3 py-2 font-medium">时间</th>
                    <th className="px-3 py-2 font-medium">操作</th>
                    <th className="px-3 py-2 font-medium">资源</th>
                    <th className="px-3 py-2 font-medium">状态</th>
                    <th className="px-3 py-2 font-medium">错误</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle">
                  {(callLogsQuery.data?.items ?? []).map((log) => (
                    <tr key={log.id} className="hover:bg-subtle/30">
                      <td className="px-3 py-2 text-xs text-tertiary whitespace-nowrap">
                        {new Date(log.created_at).toLocaleString("zh-CN")}
                      </td>
                      <td className="px-3 py-2">
                        <span className="font-mono text-xs text-foreground">{log.operation}</span>
                      </td>
                      <td className="px-3 py-2 text-xs text-secondary">{log.resource}</td>
                      <td className="px-3 py-2">
                        {log.success ? (
                          <span className="flex items-center gap-1 text-xs text-success">
                            <CheckCircle2 className="h-3 w-3" /> 成功
                          </span>
                        ) : (
                          <span className="flex items-center gap-1 text-xs text-danger">
                            <XCircle className="h-3 w-3" /> 失败
                          </span>
                        )}
                        {log.rolled_back && (
                          <span className="ml-1 text-xs text-warning">已回滚</span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-xs text-danger max-w-xs truncate">
                        {log.error || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
