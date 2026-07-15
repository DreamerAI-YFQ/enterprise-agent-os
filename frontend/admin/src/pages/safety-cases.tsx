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
import { FlaskConical, Plus, Trash2, Pencil, CheckCircle2, XCircle } from "lucide-react";

interface SafetyCase {
  id: string;
  category: string;
  prompt: string;
  expected: string;
  enabled: boolean;
  created_at?: string;
  last_result?: string;
}

export default function SafetyCasesPage() {
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<SafetyCase | null>(null);
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [createForm, setCreateForm] = useState({ category: "", prompt: "", expected: "", enabled: true });
  const [editForm, setEditForm] = useState({ category: "", prompt: "", expected: "", enabled: true });

  const query = useQuery({
    queryKey: ["admin", "safety-cases"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/safety-cases", {});
      if (error || !data) return [] as SafetyCase[];
      return data as unknown as SafetyCase[];
    },
  });

  const createMutation = useMutation({
    mutationFn: async (input: { category: string; prompt: string; expected: string; enabled: boolean }) => {
      const { error } = await apiClient.POST("/admin/safety-cases", { body: input as never });
      if (error) throw new Error("创建失败");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "safety-cases"] });
      toast.show({ title: "创建成功", description: "安全用例已添加", variant: "success" });
      setShowCreate(false);
      setCreateForm({ category: "", prompt: "", expected: "", enabled: true });
    },
    onError: () => {
      toast.show({ title: "创建失败", variant: "danger" });
    },
  });

  const updateMutation = useMutation({
    mutationFn: async (input: { id: string; body: Partial<SafetyCase> }) => {
      const { error } = await apiClient.PUT("/admin/safety-cases/{case_id}", {
        params: { path: { case_id: input.id } },
        body: input.body as never,
      });
      if (error) throw new Error("更新失败");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "safety-cases"] });
      toast.show({ title: "更新成功", variant: "success" });
      setEditing(null);
    },
    onError: () => {
      toast.show({ title: "更新失败", variant: "danger" });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      const { error } = await apiClient.DELETE("/admin/safety-cases/{case_id}", {
        params: { path: { case_id: id } },
      });
      if (error) throw new Error("删除失败");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "safety-cases"] });
      toast.show({ title: "已删除", description: "安全用例已移除", variant: "default" });
    },
    onError: () => {
      toast.show({ title: "删除失败", variant: "danger" });
    },
  });

  const toggleEnabled = (sc: SafetyCase) => {
    updateMutation.mutate({ id: sc.id, body: { enabled: !sc.enabled } });
  };

  const handleEdit = (sc: SafetyCase) => {
    setEditing(sc);
    setEditForm({ category: sc.category, prompt: sc.prompt, expected: sc.expected, enabled: sc.enabled });
  };

  const handleSaveEdit = () => {
    if (!editing) return;
    updateMutation.mutate({ id: editing.id, body: editForm });
  };

  const handleCreate = () => {
    if (!createForm.category.trim() || !createForm.prompt.trim()) {
      toast.show({ title: "请填写分类和测试提示词", variant: "danger" });
      return;
    }
    createMutation.mutate(createForm);
  };

  const allCases = query.data ?? [];
  const categories = [...new Set(allCases.map((c) => c.category))].sort();
  const filtered = categoryFilter === "all" ? allCases : allCases.filter((c) => c.category === categoryFilter);
  const enabledCount = allCases.filter((c) => c.enabled).length;

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">安全评估</h1>
            <p className="mt-1 text-sm text-secondary">
              Safety Cases — Agent 安全基准测试与通过率统计
            </p>
          </div>
          <Button onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4" />
            添加用例
          </Button>
        </div>
      </div>

      {/* Summary Stats */}
      <div className="flex items-center gap-4 border-b border-border-subtle px-8 py-3">
        <div className="flex items-center gap-2 text-xs text-tertiary">
          <span>总计 <span className="font-semibold text-foreground">{allCases.length}</span></span>
          <span>·</span>
          <span>启用 <span className="font-semibold text-success">{enabledCount}</span></span>
          <span>·</span>
          <span>停用 <span className="font-semibold text-tertiary">{allCases.length - enabledCount}</span></span>
        </div>
        <div className="flex-1" />
        <div className="flex items-center gap-1">
          <button
            onClick={() => setCategoryFilter("all")}
            className={cn(
              "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
              categoryFilter === "all" ? "bg-accent text-white" : "text-secondary hover:bg-subtle"
            )}
          >
            全部
          </button>
          {categories.map((cat) => (
            <button
              key={cat}
              onClick={() => setCategoryFilter(cat)}
              className={cn(
                "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                categoryFilter === cat ? "bg-accent text-white" : "text-secondary hover:bg-subtle"
              )}
            >
              {cat}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6">
        {query.isLoading ? (
          <div className="flex h-40 items-center justify-center"><Spinner /></div>
        ) : filtered.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <div className="flex flex-col items-center gap-3 text-center">
              <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                <FlaskConical className="h-8 w-8" strokeWidth={1.5} />
              </div>
              <h3 className="text-lg font-medium text-foreground">暂无安全用例</h3>
              <p className="max-w-sm text-sm text-secondary">
                添加测试用例以评估 Agent 在各类安全场景下的表现
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            {filtered.map((sc) => (
              <div key={sc.id} className="rounded-md border border-border bg-elevated p-5 shadow-sm">
                <div className="flex items-start justify-between">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <Badge variant="secondary" className="text-xs">{sc.category}</Badge>
                      <button
                        onClick={() => toggleEnabled(sc)}
                        className={cn(
                          "flex items-center gap-1 text-xs transition-colors",
                          sc.enabled ? "text-success" : "text-tertiary"
                        )}
                      >
                        {sc.enabled ? <CheckCircle2 className="h-3 w-3" /> : <XCircle className="h-3 w-3" />}
                        {sc.enabled ? "启用" : "停用"}
                      </button>
                      {sc.last_result && (
                        <Badge
                          variant="outline"
                          className={cn(
                            "text-xs",
                            sc.last_result === "pass" && "border-success/30 text-success",
                            sc.last_result === "fail" && "border-danger/30 text-danger",
                          )}
                        >
                          {sc.last_result === "pass" ? "通过" : sc.last_result === "fail" ? "未通过" : sc.last_result}
                        </Badge>
                      )}
                    </div>
                    <div className="mt-3 space-y-2">
                      <div>
                        <p className="text-xs font-medium text-tertiary">测试提示词</p>
                        <p className="mt-0.5 text-sm text-foreground">{sc.prompt}</p>
                      </div>
                      <div>
                        <p className="text-xs font-medium text-tertiary">期望结果</p>
                        <p className="mt-0.5 text-sm text-secondary">{sc.expected}</p>
                      </div>
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <button
                      onClick={() => handleEdit(sc)}
                      className="rounded-md p-1.5 text-secondary transition-colors hover:bg-subtle hover:text-accent"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                    <button
                      onClick={() => deleteMutation.mutate(sc.id)}
                      className="rounded-md p-1.5 text-secondary transition-colors hover:bg-subtle hover:text-danger"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Create Dialog */}
      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>添加安全用例</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div>
              <label className="text-xs text-tertiary">分类</label>
              <Input
                value={createForm.category}
                onChange={(e) => setCreateForm({ ...createForm, category: e.target.value })}
                placeholder="例: prompt_injection / data_leak / harm_refusal"
                className="mt-1"
              />
            </div>
            <div>
              <label className="text-xs text-tertiary">测试提示词</label>
              <textarea
                value={createForm.prompt}
                onChange={(e) => setCreateForm({ ...createForm, prompt: e.target.value })}
                placeholder="输入用于测试 Agent 的提示词..."
                className="mt-1 w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm text-foreground"
                rows={3}
              />
            </div>
            <div>
              <label className="text-xs text-tertiary">期望结果</label>
              <textarea
                value={createForm.expected}
                onChange={(e) => setCreateForm({ ...createForm, expected: e.target.value })}
                placeholder="描述 Agent 应有的安全响应..."
                className="mt-1 w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm text-foreground"
                rows={2}
              />
            </div>
            <label className="flex items-center gap-2 text-sm text-secondary">
              <input
                type="checkbox"
                checked={createForm.enabled}
                onChange={(e) => setCreateForm({ ...createForm, enabled: e.target.checked })}
                className="h-4 w-4 rounded border-border accent-accent"
              />
              启用此用例
            </label>
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
            <DialogTitle>编辑安全用例</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div>
              <label className="text-xs text-tertiary">分类</label>
              <Input
                value={editForm.category}
                onChange={(e) => setEditForm({ ...editForm, category: e.target.value })}
                className="mt-1"
              />
            </div>
            <div>
              <label className="text-xs text-tertiary">测试提示词</label>
              <textarea
                value={editForm.prompt}
                onChange={(e) => setEditForm({ ...editForm, prompt: e.target.value })}
                className="mt-1 w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm text-foreground"
                rows={3}
              />
            </div>
            <div>
              <label className="text-xs text-tertiary">期望结果</label>
              <textarea
                value={editForm.expected}
                onChange={(e) => setEditForm({ ...editForm, expected: e.target.value })}
                className="mt-1 w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm text-foreground"
                rows={2}
              />
            </div>
            <label className="flex items-center gap-2 text-sm text-secondary">
              <input
                type="checkbox"
                checked={editForm.enabled}
                onChange={(e) => setEditForm({ ...editForm, enabled: e.target.checked })}
                className="h-4 w-4 rounded border-border accent-accent"
              />
              启用此用例
            </label>
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
    </div>
  );
}
