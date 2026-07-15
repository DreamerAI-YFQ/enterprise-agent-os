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
import { FileText, Plus, Trash2, Pencil, Eye } from "lucide-react";

interface ReportTemplate {
  id: string;
  name: string;
  description: string;
  template_type: string;
  content: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

const TEMPLATE_TYPES = [
  { value: "generic", label: "通用" },
  { value: "financial", label: "财务报告" },
  { value: "operation", label: "运营报告" },
  { value: "custom", label: "自定义" },
];

export default function ReportTemplatesPage() {
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<ReportTemplate | null>(null);
  const [previewing, setPreviewing] = useState<ReportTemplate | null>(null);
  const [createForm, setCreateForm] = useState({ name: "", description: "", template_type: "generic", content_json: "{}" });
  const [editForm, setEditForm] = useState({ name: "", description: "", template_type: "generic", content_json: "{}" });

  const query = useQuery({
    queryKey: ["admin", "report-templates"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/report-templates", {});
      if (error || !data) return [] as ReportTemplate[];
      return data as unknown as ReportTemplate[];
    },
  });

  const createMutation = useMutation({
    mutationFn: async (input: { name: string; description: string; template_type: string; content: Record<string, unknown> }) => {
      const { error } = await apiClient.POST("/admin/report-templates", { body: input as never });
      if (error) throw new Error("创建失败");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "report-templates"] });
      toast.show({ title: "创建成功", description: "报告模板已添加", variant: "success" });
      setShowCreate(false);
      setCreateForm({ name: "", description: "", template_type: "generic", content_json: "{}" });
    },
    onError: () => {
      toast.show({ title: "创建失败", variant: "danger" });
    },
  });

  const updateMutation = useMutation({
    mutationFn: async (input: { id: string; body: Record<string, unknown> }) => {
      const { error } = await apiClient.PUT("/admin/report-templates/{template_id}", {
        params: { path: { template_id: input.id } },
        body: input.body as never,
      });
      if (error) throw new Error("更新失败");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "report-templates"] });
      toast.show({ title: "更新成功", variant: "success" });
      setEditing(null);
    },
    onError: () => {
      toast.show({ title: "更新失败", variant: "danger" });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      const { error } = await apiClient.DELETE("/admin/report-templates/{template_id}", {
        params: { path: { template_id: id } },
      });
      if (error) throw new Error("删除失败");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "report-templates"] });
      toast.show({ title: "已删除", description: "模板已移除", variant: "default" });
    },
    onError: () => {
      toast.show({ title: "删除失败", variant: "danger" });
    },
  });

  const handleEdit = (t: ReportTemplate) => {
    setEditing(t);
    setEditForm({
      name: t.name,
      description: t.description,
      template_type: t.template_type,
      content_json: JSON.stringify(t.content ?? {}, null, 2),
    });
  };

  const handleSaveEdit = () => {
    if (!editing) return;
    let content: Record<string, unknown> = {};
    try {
      content = JSON.parse(editForm.content_json);
    } catch {
      toast.show({ title: "JSON 格式错误", variant: "danger" });
      return;
    }
    updateMutation.mutate({
      id: editing.id,
      body: { name: editForm.name, description: editForm.description, template_type: editForm.template_type, content },
    });
  };

  const handleCreate = () => {
    if (!createForm.name.trim()) {
      toast.show({ title: "请填写模板名称", variant: "danger" });
      return;
    }
    let content: Record<string, unknown> = {};
    try {
      content = JSON.parse(createForm.content_json);
    } catch {
      toast.show({ title: "JSON 格式错误", description: "请检查 content 格式", variant: "danger" });
      return;
    }
    createMutation.mutate({ ...createForm, content });
  };

  const templates = query.data ?? [];

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">报告模板</h1>
            <p className="mt-1 text-sm text-secondary">
              模板 CRUD + 变量映射 + 预览
            </p>
          </div>
          <Button onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4" />
            添加模板
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6">
        {query.isLoading ? (
          <div className="flex h-40 items-center justify-center"><Spinner /></div>
        ) : templates.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <div className="flex flex-col items-center gap-3 text-center">
              <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                <FileText className="h-8 w-8" strokeWidth={1.5} />
              </div>
              <h3 className="text-lg font-medium text-foreground">暂无报告模板</h3>
              <p className="text-sm text-secondary">
                创建报告模板以标准化 Agent 输出格式
              </p>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-4">
            {templates.map((t) => {
              const typeLabel = TEMPLATE_TYPES.find((x) => x.value === t.template_type)?.label ?? t.template_type;
              const contentKeys = Object.keys(t.content ?? {});
              return (
                <div key={t.id} className="rounded-md border border-border bg-elevated p-5 shadow-sm">
                  <div className="flex items-start justify-between">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <h3 className="text-sm font-semibold text-foreground">{t.name}</h3>
                        <Badge variant="secondary" className="text-xs">{typeLabel}</Badge>
                      </div>
                      <p className="mt-1.5 text-sm text-secondary line-clamp-2">
                        {t.description || "无描述"}
                      </p>
                      {contentKeys.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1">
                          {contentKeys.slice(0, 5).map((k) => (
                            <span key={k} className="rounded bg-subtle px-1.5 py-0.5 text-xs text-tertiary">
                              {k}
                            </span>
                          ))}
                          {contentKeys.length > 5 && (
                            <span className="text-xs text-tertiary">+{contentKeys.length - 5}</span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="mt-4 flex items-center gap-1 border-t border-border-subtle pt-3">
                    <button
                      onClick={() => setPreviewing(t)}
                      className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-secondary transition-colors hover:bg-subtle hover:text-accent"
                    >
                      <Eye className="h-3 w-3" /> 预览
                    </button>
                    <button
                      onClick={() => handleEdit(t)}
                      className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-secondary transition-colors hover:bg-subtle hover:text-accent"
                    >
                      <Pencil className="h-3 w-3" /> 编辑
                    </button>
                    <div className="flex-1" />
                    <button
                      onClick={() => deleteMutation.mutate(t.id)}
                      className="rounded-md p-1 text-secondary transition-colors hover:bg-subtle hover:text-danger"
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
            <DialogTitle>添加报告模板</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div>
              <label className="text-xs text-tertiary">模板名称</label>
              <Input
                value={createForm.name}
                onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })}
                placeholder="例: 月度运营报告"
                className="mt-1"
              />
            </div>
            <div>
              <label className="text-xs text-tertiary">描述</label>
              <Input
                value={createForm.description}
                onChange={(e) => setCreateForm({ ...createForm, description: e.target.value })}
                placeholder="模板用途说明"
                className="mt-1"
              />
            </div>
            <div>
              <label className="text-xs text-tertiary">模板类型</label>
              <select
                value={createForm.template_type}
                onChange={(e) => setCreateForm({ ...createForm, template_type: e.target.value })}
                className="mt-1 w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm text-foreground"
              >
                {TEMPLATE_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-xs text-tertiary">内容 (JSON)</label>
              <textarea
                value={createForm.content_json}
                onChange={(e) => setCreateForm({ ...createForm, content_json: e.target.value })}
                placeholder='{"title": "", "sections": [], "variables": {}}'
                className="mt-1 w-full rounded-md border border-border bg-transparent px-3 py-2 font-mono text-xs text-foreground"
                rows={5}
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

      {/* Edit Dialog */}
      <Dialog open={!!editing} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>编辑模板</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div>
              <label className="text-xs text-tertiary">模板名称</label>
              <Input
                value={editForm.name}
                onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                className="mt-1"
              />
            </div>
            <div>
              <label className="text-xs text-tertiary">描述</label>
              <Input
                value={editForm.description}
                onChange={(e) => setEditForm({ ...editForm, description: e.target.value })}
                className="mt-1"
              />
            </div>
            <div>
              <label className="text-xs text-tertiary">模板类型</label>
              <select
                value={editForm.template_type}
                onChange={(e) => setEditForm({ ...editForm, template_type: e.target.value })}
                className="mt-1 w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm text-foreground"
              >
                {TEMPLATE_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>{t.label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-xs text-tertiary">内容 (JSON)</label>
              <textarea
                value={editForm.content_json}
                onChange={(e) => setEditForm({ ...editForm, content_json: e.target.value })}
                className="mt-1 w-full rounded-md border border-border bg-transparent px-3 py-2 font-mono text-xs text-foreground"
                rows={5}
              />
            </div>
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">取消</Button>
            </DialogClose>
            <Button onClick={handleSaveEdit} disabled={updateMutation.isPending}>
              {updateMutation.isPending ? "保存中..." : "保存"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Preview Dialog */}
      <Dialog open={!!previewing} onOpenChange={(open) => !open && setPreviewing(null)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{previewing?.name}</DialogTitle>
          </DialogHeader>
          <div className="py-2">
            {previewing && (
              <div className="space-y-3">
                <p className="text-sm text-secondary">{previewing.description}</p>
                <div className="rounded-md border border-border-subtle bg-subtle/20 p-4">
                  <p className="mb-2 text-xs font-medium text-tertiary">模板内容</p>
                  <pre className="overflow-x-auto text-xs text-secondary">
                    {JSON.stringify(previewing.content, null, 2)}
                  </pre>
                </div>
              </div>
            )}
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">关闭</Button>
            </DialogClose>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
