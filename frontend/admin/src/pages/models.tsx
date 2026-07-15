import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import { Button, Input, Spinner, Badge, toast } from "@eaos/shared";
import { Cpu, Save, Plus, Trash2, Settings2 } from "lucide-react";

interface ModelsConfig {
  default_model: string;
  providers: Record<string, ProviderConfig>;
}

interface ProviderConfig {
  model: string;
  api_base?: string;
  api_key?: string;
  temperature?: number;
  max_tokens?: number;
  [key: string]: unknown;
}

export default function ModelsPage() {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [config, setConfig] = useState<ModelsConfig>({
    default_model: "",
    providers: {},
  });

  const query = useQuery({
    queryKey: ["admin", "models"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/models", {});
      if (error || !data) return null;
      return data as unknown as ModelsConfig;
    },
  });

  useEffect(() => {
    if (query.data) {
      setConfig({
        default_model: query.data.default_model ?? "",
        providers: query.data.providers ?? {},
      });
    }
  }, [query.data]);

  const saveMutation = useMutation({
    mutationFn: async (cfg: ModelsConfig) => {
      const { error } = await apiClient.PUT("/admin/models", {
        body: cfg as never,
      });
      if (error) throw new Error("保存失败");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "models"] });
      toast.show({ title: "保存成功", description: "模型配置已更新", variant: "success" });
      setEditing(false);
    },
    onError: () => {
      toast.show({ title: "保存失败", description: "请检查配置后重试", variant: "danger" });
    },
  });

  const handleSave = () => {
    saveMutation.mutate(config);
  };

  const addProvider = () => {
    const name = `provider_${Object.keys(config.providers).length + 1}`;
    setConfig({
      ...config,
      providers: { ...config.providers, [name]: { model: "" } },
    });
  };

  const updateProvider = (name: string, field: string, value: string) => {
    setConfig({
      ...config,
      providers: {
        ...config.providers,
        [name]: { ...config.providers[name], [field]: value },
      },
    });
  };

  const removeProvider = (name: string) => {
    const next = { ...config.providers };
    delete next[name];
    setConfig({ ...config, providers: next });
  };

  const providerNames = Object.keys(config.providers ?? {});
  const current = query.data;

  if (query.isLoading && !current) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">模型管理</h1>
            <p className="mt-1 text-sm text-secondary">
              LLM Provider 配置、模型路由与 API Key 管理
            </p>
          </div>
          <div className="flex items-center gap-2">
            {editing ? (
              <>
                <Button variant="outline" onClick={() => { setEditing(false); if (current) setConfig({ default_model: current.default_model ?? "", providers: current.providers ?? {} }); }}>
                  取消
                </Button>
                <Button onClick={handleSave} disabled={saveMutation.isPending}>
                  <Save className="h-4 w-4" />
                  {saveMutation.isPending ? "保存中..." : "保存"}
                </Button>
              </>
            ) : (
              <Button variant="outline" onClick={() => setEditing(true)}>
                <Settings2 className="h-4 w-4" />
                编辑配置
              </Button>
            )}
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6">
        {/* Default Model */}
        <div className="mb-6 rounded-md border border-border bg-elevated p-6 shadow-sm">
          <div className="mb-4 flex items-center gap-2">
            <Cpu className="h-4 w-4 text-accent" />
            <h2 className="text-sm font-medium text-foreground">默认模型</h2>
          </div>
          {editing ? (
            <Input
              value={config.default_model}
              onChange={(e) => setConfig({ ...config, default_model: e.target.value })}
              placeholder="例: gpt-4o / claude-3-opus / glm-4"
              className="max-w-md"
            />
          ) : (
            <div className="flex items-center gap-2">
              <p className="text-lg font-semibold text-foreground">
                {config.default_model || "—"}
              </p>
              <Badge variant="secondary" className="text-xs">活跃</Badge>
            </div>
          )}
        </div>

        {/* Provider List */}
        <div className="rounded-md border border-border bg-elevated p-6 shadow-sm">
          <div className="mb-4 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Settings2 className="h-4 w-4 text-accent" />
              <h2 className="text-sm font-medium text-foreground">Provider 配置</h2>
              <Badge variant="outline" className="text-xs">{providerNames.length} 个</Badge>
            </div>
            {editing && (
              <Button size="sm" variant="outline" onClick={addProvider}>
                <Plus className="h-3.5 w-3.5" />
                添加 Provider
              </Button>
            )}
          </div>

          {providerNames.length === 0 ? (
            <p className="py-8 text-center text-sm text-tertiary">
              暂无 Provider 配置{editing ? "，点击上方按钮添加" : ""}
            </p>
          ) : (
            <div className="space-y-4">
              {providerNames.map((name) => {
                const p = config.providers[name];
                return (
                  <div key={name} className="rounded-md border border-border-subtle bg-subtle/30 p-4">
                    <div className="mb-3 flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-foreground">{name}</span>
                        {p.model && <Badge variant="outline" className="text-xs">{p.model}</Badge>}
                      </div>
                      {editing && (
                        <button
                          onClick={() => removeProvider(name)}
                          className="text-tertiary hover:text-danger"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      )}
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="text-xs text-tertiary">模型名称</label>
                        {editing ? (
                          <Input
                            value={p.model ?? ""}
                            onChange={(e) => updateProvider(name, "model", e.target.value)}
                            placeholder="gpt-4o-mini"
                            className="mt-1"
                          />
                        ) : (
                          <p className="mt-1 text-sm text-foreground">{p.model || "—"}</p>
                        )}
                      </div>
                      <div>
                        <label className="text-xs text-tertiary">API Base</label>
                        {editing ? (
                          <Input
                            value={(p.api_base as string) ?? ""}
                            onChange={(e) => updateProvider(name, "api_base", e.target.value)}
                            placeholder="https://api.openai.com/v1"
                            className="mt-1"
                          />
                        ) : (
                          <p className="mt-1 text-sm text-foreground truncate">{p.api_base || "默认"}</p>
                        )}
                      </div>
                      <div>
                        <label className="text-xs text-tertiary">API Key</label>
                        {editing ? (
                          <Input
                            type="password"
                            value={(p.api_key as string) ?? ""}
                            onChange={(e) => updateProvider(name, "api_key", e.target.value)}
                            placeholder="sk-..."
                            className="mt-1"
                          />
                        ) : (
                          <p className="mt-1 text-sm text-foreground">
                            {p.api_key ? "••••••••" : "未设置"}
                          </p>
                        )}
                      </div>
                      <div>
                        <label className="text-xs text-tertiary">Temperature</label>
                        {editing ? (
                          <Input
                            type="number"
                            step="0.1"
                            min="0"
                            max="2"
                            value={String(p.temperature ?? "")}
                            onChange={(e) => updateProvider(name, "temperature", e.target.value)}
                            placeholder="0.7"
                            className="mt-1"
                          />
                        ) : (
                          <p className="mt-1 text-sm text-foreground">
                            {p.temperature != null ? String(p.temperature) : "默认"}
                          </p>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* JSON Preview (read-only) */}
        {!editing && current && (
          <div className="mt-6 rounded-md border border-border-subtle bg-subtle/20 p-4">
            <p className="mb-2 text-xs font-medium text-tertiary">原始配置 (JSON)</p>
            <pre className="overflow-x-auto text-xs text-secondary">
              {JSON.stringify(current, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
