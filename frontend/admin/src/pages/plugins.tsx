import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import { Button, Input, Spinner, Badge, cn, toast } from "@eaos/shared";
import { Puzzle, Save, Plus, Trash2, Settings2, Power } from "lucide-react";

interface PluginsConfig {
  plugins: Record<string, PluginEntry>;
}

interface PluginEntry {
  enabled: boolean;
  config?: Record<string, unknown>;
  [key: string]: unknown;
}

export default function PluginsPage() {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [config, setConfig] = useState<PluginsConfig>({ plugins: {} });
  const [newPluginName, setNewPluginName] = useState("");

  const query = useQuery({
    queryKey: ["admin", "plugins"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/plugins", {});
      if (error || !data) return null;
      return data as unknown as PluginsConfig;
    },
  });

  useEffect(() => {
    if (query.data) {
      setConfig({ plugins: query.data.plugins ?? {} });
    }
  }, [query.data]);

  const saveMutation = useMutation({
    mutationFn: async (cfg: PluginsConfig) => {
      const { error } = await apiClient.PUT("/admin/plugins", {
        body: cfg as never,
      });
      if (error) throw new Error("保存失败");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "plugins"] });
      toast.show({ title: "保存成功", description: "插件配置已更新", variant: "success" });
      setEditing(false);
    },
    onError: () => {
      toast.show({ title: "保存失败", variant: "danger" });
    },
  });

  const handleSave = () => {
    saveMutation.mutate(config);
  };

  const togglePlugin = (name: string) => {
    const p = config.plugins[name];
    setConfig({
      ...config,
      plugins: { ...config.plugins, [name]: { ...p, enabled: !p?.enabled } },
    });
  };

  const addPlugin = () => {
    if (!newPluginName.trim()) return;
    if (config.plugins[newPluginName]) {
      toast.show({ title: "插件已存在", variant: "danger" });
      return;
    }
    setConfig({
      ...config,
      plugins: { ...config.plugins, [newPluginName]: { enabled: false } },
    });
    setNewPluginName("");
  };

  const removePlugin = (name: string) => {
    const next = { ...config.plugins };
    delete next[name];
    setConfig({ ...config, plugins: next });
  };

  const updatePluginConfig = (name: string, json: string) => {
    let parsed: Record<string, unknown> = {};
    try {
      parsed = JSON.parse(json);
    } catch {
      // keep as-is while editing
    }
    setConfig({
      ...config,
      plugins: { ...config.plugins, [name]: { ...config.plugins[name], config: parsed } },
    });
  };

  const pluginNames = Object.keys(config.plugins ?? {});
  const current = query.data;

  if (query.isLoading && !current) {
    return (
      <div className="flex h-full items-center justify-center"><Spinner /></div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">插件配置</h1>
            <p className="mt-1 text-sm text-secondary">
              插件启停 + 配置项编辑
            </p>
          </div>
          {editing ? (
            <div className="flex items-center gap-2">
              <Button variant="outline" onClick={() => { setEditing(false); if (current) setConfig({ plugins: current.plugins ?? {} }); }}>
                取消
              </Button>
              <Button onClick={handleSave} disabled={saveMutation.isPending}>
                <Save className="h-4 w-4" />
                {saveMutation.isPending ? "保存中..." : "保存"}
              </Button>
            </div>
          ) : (
            <Button variant="outline" onClick={() => setEditing(true)}>
              <Settings2 className="h-4 w-4" />
              编辑配置
            </Button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6">
        {/* Add Plugin (edit mode) */}
        {editing && (
          <div className="mb-6 rounded-md border border-border-subtle bg-subtle/30 p-4">
            <div className="flex items-center gap-2">
              <Input
                value={newPluginName}
                onChange={(e) => setNewPluginName(e.target.value)}
                placeholder="新插件名称（如: analytics / export / notification）"
                className="flex-1"
              />
              <Button size="sm" onClick={addPlugin}>
                <Plus className="h-3.5 w-3.5" />
                添加
              </Button>
            </div>
          </div>
        )}

        {/* Plugin List */}
        {pluginNames.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <div className="flex flex-col items-center gap-3 text-center">
              <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                <Puzzle className="h-8 w-8" strokeWidth={1.5} />
              </div>
              <h3 className="text-lg font-medium text-foreground">暂无插件</h3>
              <p className="text-sm text-secondary">
                {editing ? "添加插件以扩展系统功能" : "当前未配置任何插件"}
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            {pluginNames.map((name) => {
              const p = config.plugins[name];
              const enabled = p?.enabled ?? false;
              return (
                <div key={name} className="rounded-md border border-border bg-elevated p-5 shadow-sm">
                  <div className="flex items-start justify-between">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <Puzzle className="h-4 w-4 text-accent" />
                        <h3 className="text-sm font-semibold text-foreground">{name}</h3>
                        <Badge
                          variant="outline"
                          className={cn("text-xs", enabled && "border-success/30 text-success")}
                        >
                          {enabled ? "启用" : "停用"}
                        </Badge>
                      </div>
                      {p.config && Object.keys(p.config).length > 0 && (
                        <div className="mt-2">
                          {editing ? (
                            <textarea
                              defaultValue={JSON.stringify(p.config, null, 2)}
                              onBlur={(e) => updatePluginConfig(name, e.target.value)}
                              className="w-full rounded-md border border-border bg-transparent px-3 py-2 font-mono text-xs text-foreground"
                              rows={4}
                            />
                          ) : (
                            <pre className="overflow-x-auto rounded bg-subtle/30 p-2 text-xs text-tertiary">
                              {JSON.stringify(p.config, null, 2)}
                            </pre>
                          )}
                        </div>
                      )}
                      {editing && !p.config && (
                        <textarea
                          placeholder='{"key": "value"}'
                          onBlur={(e) => e.target.value && updatePluginConfig(name, e.target.value)}
                          className="mt-2 w-full rounded-md border border-border bg-transparent px-3 py-2 font-mono text-xs text-foreground"
                          rows={3}
                        />
                      )}
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      {editing && (
                        <>
                          <button
                            onClick={() => togglePlugin(name)}
                            className={cn(
                              "flex items-center gap-1 rounded-md border border-border px-2.5 py-1 text-xs font-medium transition-colors",
                              enabled
                                ? "text-warning hover:bg-subtle"
                                : "text-success hover:bg-subtle"
                            )}
                          >
                            <Power className="h-3 w-3" />
                            {enabled ? "停用" : "启用"}
                          </button>
                          <button
                            onClick={() => removePlugin(name)}
                            className="rounded-md border border-border p-1.5 text-secondary transition-colors hover:bg-subtle hover:text-danger"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}

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
