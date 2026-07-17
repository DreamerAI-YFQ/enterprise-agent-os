import { useMemo, useState } from "react";
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
  SearchInput,
  Pagination,
  LoadingState,
} from "@eaos/shared";
import {
  Users,
  Plus,
  Trash2,
  Pencil,
  Shield,
  User as UserIcon,
  Building2,
  Power,
} from "lucide-react";

interface User {
  id: string;
  email: string;
  name: string;
  role: string;
  status: string;
  tenant_id?: string;
  created_at?: string;
}

interface Department {
  id: string;
  name: string;
  parent_id: string | null;
}

interface UserDepartment {
  id: string;
  name: string;
  member_role: string;
}

const ROLE_LABELS: Record<string, string> = {
  admin: "管理员",
  manager: "经理",
  employee: "员工",
  viewer: "观察者",
};

const STATUS_LABELS: Record<string, string> = {
  active: "活跃",
  inactive: "停用",
  suspended: "冻结",
};

export default function UsersPage() {
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<User | null>(null);
  const [deletingUser, setDeletingUser] = useState<User | null>(null);
  const [deptAssignUser, setDeptAssignUser] = useState<User | null>(null);
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("all");
  const [page, setPage] = useState(1);
  const [createForm, setCreateForm] = useState({
    email: "",
    name: "",
    password: "",
    role: "employee",
    status: "active",
    departmentIds: [] as string[],
  });
  const [editForm, setEditForm] = useState({ name: "", role: "", status: "", password: "" });

  const query = useQuery({
    queryKey: ["admin", "users"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/users", {});
      if (error || !data) return [] as User[];
      return data as unknown as User[];
    },
  });

  const createMutation = useMutation({
    mutationFn: async (input: {
      email: string;
      name: string;
      password: string;
      role: string;
      status: string;
      departmentIds: string[];
    }) => {
      const { data, error } = await apiClient.POST("/admin/users", {
        body: {
          email: input.email,
          name: input.name,
          password: input.password,
          role: input.role,
          status: input.status,
        } as never,
      });
      if (error) throw new Error("创建失败");
      const userId = (data as never as User)?.id;
      if (userId && input.departmentIds.length > 0) {
        await Promise.all(
          input.departmentIds.map((deptId) =>
            apiClient.POST("/admin/departments/{department_id}/members", {
              params: { path: { department_id: deptId } },
              body: { user_id: userId, role: "member" } as never,
            }),
          ),
        );
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      toast.show({ title: "邀请成功", description: "用户已添加", variant: "success" });
      setShowCreate(false);
      setCreateForm({
        email: "",
        name: "",
        password: "",
        role: "employee",
        status: "active",
        departmentIds: [],
      });
    },
    onError: () => {
      toast.show({ title: "创建失败", description: "邮箱可能已存在", variant: "danger" });
    },
  });

  const updateMutation = useMutation({
    mutationFn: async (input: {
      userId: string;
      body: { name: string; role: string; status: string; password?: string };
    }) => {
      const { error } = await apiClient.PUT("/admin/users/{user_id}", {
        params: { path: { user_id: input.userId } },
        body: input.body as never,
      });
      if (error) throw new Error("更新失败");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      toast.show({ title: "更新成功", description: "用户信息已修改", variant: "success" });
      setEditing(null);
    },
    onError: () => {
      toast.show({ title: "更新失败", variant: "danger" });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      const { error } = await apiClient.DELETE("/admin/users/{user_id}", {
        params: { path: { user_id: id } },
      });
      if (error) throw new Error("删除失败");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      toast.show({ title: "已删除", description: "用户已移除", variant: "default" });
      setDeletingUser(null);
    },
    onError: () => {
      toast.show({ title: "删除失败", variant: "danger" });
    },
  });

  const toggleStatusMutation = useMutation({
    mutationFn: async (user: User) => {
      const newStatus = user.status === "active" ? "inactive" : "active";
      const { error } = await apiClient.PUT("/admin/users/{user_id}", {
        params: { path: { user_id: user.id } },
        body: { name: user.name, role: user.role, status: newStatus } as never,
      });
      if (error) throw new Error("操作失败");
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "users"] });
      toast.show({ title: "状态已更新", variant: "success" });
    },
    onError: () => {
      toast.show({ title: "操作失败", variant: "danger" });
    },
  });

  const handleEdit = (u: User) => {
    setEditing(u);
    setEditForm({ name: u.name, role: u.role, status: u.status, password: "" });
  };

  const handleSaveEdit = () => {
    if (!editing) return;
    if (editForm.password && editForm.password.length < 8) {
      toast.show({ title: "新密码至少需要 8 个字符", variant: "danger" });
      return;
    }
    updateMutation.mutate({
      userId: editing.id,
      body: {
        name: editForm.name,
        role: editForm.role,
        status: editForm.status,
        ...(editForm.password ? { password: editForm.password } : {}),
      },
    });
  };

  const handleCreate = () => {
    if (!createForm.email.trim() || !createForm.name.trim() || !createForm.password) {
      toast.show({ title: "请填写邮箱、姓名和初始密码", variant: "danger" });
      return;
    }
    if (createForm.password.length < 8) {
      toast.show({ title: "初始密码至少需要 8 个字符", variant: "danger" });
      return;
    }
    createMutation.mutate(createForm);
  };

  const allUsers = useMemo(() => query.data ?? [], [query.data]);
  const filtered = useMemo(() => allUsers.filter((u) => {
    const matchesSearch =
      !search ||
      u.name?.toLowerCase().includes(search.toLowerCase()) ||
      u.email?.toLowerCase().includes(search.toLowerCase());
    const matchesRole = roleFilter === "all" || u.role === roleFilter;
    return matchesSearch && matchesRole;
  }), [allUsers, search, roleFilter]);
  const PAGE_SIZE = 15;
  const total = filtered.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const safePage = Math.min(page, totalPages);
  const pagedUsers = useMemo(() => {
    const start = (safePage - 1) * PAGE_SIZE;
    return filtered.slice(start, start + PAGE_SIZE);
  }, [filtered, safePage]);

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">用户管理</h1>
            <p className="mt-1 text-sm text-secondary">
              用户邀请 + 角色分配 + 部门管理 + 启用/禁用
            </p>
          </div>
          <Button onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4" />
            邀请用户
          </Button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 border-b border-border-subtle px-8 py-3">
        <SearchInput
          value={search}
          onChange={(v) => {
            setSearch(v);
            setPage(1);
          }}
          placeholder="搜索姓名或邮箱..."
          className="max-w-xs"
        />
        <div className="flex items-center gap-1">
          {["all", "admin", "manager", "employee", "viewer"].map((r) => (
            <button
              key={r}
              onClick={() => {
                setRoleFilter(r);
                setPage(1);
              }}
              className={cn(
                "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                roleFilter === r
                  ? "bg-accent text-white"
                  : "text-secondary hover:bg-subtle"
              )}
            >
              {r === "all" ? "全部" : ROLE_LABELS[r] ?? r}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6">
        {query.isLoading ? (
          <LoadingState />
        ) : pagedUsers.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <div className="flex flex-col items-center gap-3 text-center">
              <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                <Users className="h-8 w-8" strokeWidth={1.5} />
              </div>
              <h3 className="text-lg font-medium text-foreground">暂无用户</h3>
              <p className="text-sm text-secondary">
                {search || roleFilter !== "all" ? "未找到匹配的用户" : "邀请第一个用户开始管理"}
              </p>
            </div>
          </div>
        ) : (
          <div className="overflow-hidden rounded-md border border-border">
            <table className="w-full">
              <thead className="bg-subtle/50">
                <tr className="text-left text-xs text-tertiary">
                  <th className="px-4 py-3 font-medium">用户</th>
                  <th className="px-4 py-3 font-medium">邮箱</th>
                  <th className="px-4 py-3 font-medium">角色</th>
                  <th className="px-4 py-3 font-medium">状态</th>
                  <th className="px-4 py-3 font-medium text-right">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {pagedUsers.map((u) => (
                  <tr key={u.id} className="bg-elevated text-sm transition-colors hover:bg-subtle/30">
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-accent/10 text-accent">
                          <UserIcon className="h-4 w-4" />
                        </div>
                        <span className="font-medium text-foreground">{u.name || "—"}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3 text-secondary">{u.email}</td>
                    <td className="px-4 py-3">
                      <Badge
                        variant="outline"
                        className={cn(
                          "text-xs",
                          u.role === "admin" && "border-accent/30 text-accent",
                          u.role === "manager" && "border-blue-400/30 text-blue-600",
                          u.role === "employee" && "border-success/30 text-success",
                        )}
                      >
                        {u.role === "admin" && <Shield className="mr-1 h-3 w-3" />}
                        {ROLE_LABELS[u.role] ?? u.role}
                      </Badge>
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={cn(
                          "inline-flex items-center gap-1 text-xs",
                          u.status === "active" && "text-success",
                          u.status === "inactive" && "text-tertiary",
                          u.status === "suspended" && "text-danger",
                        )}
                      >
                        <span className={cn(
                          "h-1.5 w-1.5 rounded-full",
                          u.status === "active" && "bg-success",
                          u.status === "inactive" && "bg-tertiary",
                          u.status === "suspended" && "bg-danger",
                        )} />
                        {STATUS_LABELS[u.status] ?? u.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          onClick={() => handleEdit(u)}
                          className="rounded-md p-1.5 text-secondary transition-colors hover:bg-subtle hover:text-accent"
                          title="编辑"
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={() => setDeptAssignUser(u)}
                          className="rounded-md p-1.5 text-secondary transition-colors hover:bg-subtle hover:text-accent"
                          title="分配部门"
                        >
                          <Building2 className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={() => toggleStatusMutation.mutate(u)}
                          className={cn(
                            "rounded-md p-1.5 transition-colors hover:bg-subtle",
                            u.status === "active"
                              ? "text-secondary hover:text-danger"
                              : "text-secondary hover:text-success"
                          )}
                          title={u.status === "active" ? "禁用" : "启用"}
                        >
                          <Power className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={() => setDeletingUser(u)}
                          className="rounded-md p-1.5 text-secondary transition-colors hover:bg-subtle hover:text-danger"
                          title="删除"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {total > PAGE_SIZE && (
          <Pagination
            page={safePage}
            pageSize={PAGE_SIZE}
            total={total}
            onPageChange={setPage}
          />
        )}
      </div>

      {/* Create Dialog */}
      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>邀请用户</DialogTitle>
          </DialogHeader>
          <CreateUserForm
            form={createForm}
            setForm={setCreateForm}
          />
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">取消</Button>
            </DialogClose>
            <Button onClick={handleCreate} disabled={createMutation.isPending}>
              {createMutation.isPending ? "创建中..." : "邀请"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit Dialog */}
      <Dialog open={!!editing} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>编辑用户</DialogTitle>
          </DialogHeader>
          {editing && (
            <div className="space-y-4 py-2">
              <div className="rounded-md bg-subtle/30 px-3 py-2 text-xs text-tertiary">
                {editing.email}
              </div>
              <div>
                <label className="text-xs text-tertiary">姓名</label>
                <Input
                  value={editForm.name}
                  onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
                  className="mt-1"
                />
              </div>
              <div>
                <label className="text-xs text-tertiary">重置密码（留空则不修改）</label>
                <Input
                  type="password"
                  value={editForm.password}
                  onChange={(e) => setEditForm({ ...editForm, password: e.target.value })}
                  autoComplete="new-password"
                  minLength={8}
                  className="mt-1"
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-tertiary">角色</label>
                  <select
                    value={editForm.role}
                    onChange={(e) => setEditForm({ ...editForm, role: e.target.value })}
                    className="mt-1 w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm text-foreground"
                  >
                    <option value="employee">员工</option>
                    <option value="manager">经理</option>
                    <option value="admin">管理员</option>
                    <option value="viewer">观察者</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-tertiary">状态</label>
                  <select
                    value={editForm.status}
                    onChange={(e) => setEditForm({ ...editForm, status: e.target.value })}
                    className="mt-1 w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm text-foreground"
                  >
                    <option value="active">活跃</option>
                    <option value="inactive">停用</option>
                    <option value="suspended">冻结</option>
                  </select>
                </div>
              </div>
            </div>
          )}
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

      {/* Delete Confirmation Dialog */}
      <Dialog open={!!deletingUser} onOpenChange={(open) => !open && setDeletingUser(null)}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>确认删除用户</DialogTitle>
          </DialogHeader>
          {deletingUser && (
            <div className="py-2 text-sm text-secondary">
              确定要删除用户 <span className="font-medium text-foreground">{deletingUser.name}</span>
              {" "}
              ({deletingUser.email}) 吗？此操作不可撤销，用户的所有数据将被移除。
            </div>
          )}
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">取消</Button>
            </DialogClose>
            <Button
              variant="danger"
              onClick={() => deletingUser && deleteMutation.mutate(deletingUser.id)}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? "删除中..." : "确认删除"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Department Assignment Dialog */}
      {deptAssignUser && (
        <DepartmentAssignDialog
          user={deptAssignUser}
          onClose={() => setDeptAssignUser(null)}
        />
      )}
    </div>
  );
}

// ============================================================
// Create User Form (with department multi-select)
// ============================================================

function CreateUserForm({
  form,
  setForm,
}: {
  form: {
    email: string;
    name: string;
    password: string;
    role: string;
    status: string;
    departmentIds: string[];
  };
  setForm: (f: typeof form) => void;
}) {
  const { data: departments } = useQuery({
    queryKey: ["departments"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/departments", {});
      if (error || !data) return [] as Department[];
      return data as unknown as Department[];
    },
  });

  const toggleDept = (deptId: string) => {
    setForm({
      ...form,
      departmentIds: form.departmentIds.includes(deptId)
        ? form.departmentIds.filter((id) => id !== deptId)
        : [...form.departmentIds, deptId],
    });
  };

  return (
    <div className="space-y-4 py-2">
      <div>
        <label className="text-xs text-tertiary">邮箱</label>
        <Input
          type="email"
          value={form.email}
          onChange={(e) => setForm({ ...form, email: e.target.value })}
          placeholder="user@acme.com"
          className="mt-1"
        />
      </div>
      <div>
        <label className="text-xs text-tertiary">姓名</label>
        <Input
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
          placeholder="张三"
          className="mt-1"
        />
      </div>
      <div>
        <label className="text-xs text-tertiary">初始密码</label>
        <Input
          type="password"
          value={form.password}
          onChange={(e) => setForm({ ...form, password: e.target.value })}
          autoComplete="new-password"
          minLength={8}
          placeholder="为用户设置初始密码"
          className="mt-1"
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-xs text-tertiary">角色</label>
          <select
            value={form.role}
            onChange={(e) => setForm({ ...form, role: e.target.value })}
            className="mt-1 w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm text-foreground"
          >
            <option value="employee">员工</option>
            <option value="manager">经理</option>
            <option value="admin">管理员</option>
            <option value="viewer">观察者</option>
          </select>
        </div>
        <div>
          <label className="text-xs text-tertiary">状态</label>
          <select
            value={form.status}
            onChange={(e) => setForm({ ...form, status: e.target.value })}
            className="mt-1 w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm text-foreground"
          >
            <option value="active">活跃</option>
            <option value="inactive">停用</option>
          </select>
        </div>
      </div>
      {departments && departments.length > 0 && (
        <div>
          <label className="text-xs text-tertiary">分配部门（可选）</label>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {departments.map((dept) => (
              <button
                key={dept.id}
                type="button"
                onClick={() => toggleDept(dept.id)}
                className={cn(
                  "rounded-md border px-2.5 py-1 text-xs transition-colors",
                  form.departmentIds.includes(dept.id)
                    ? "border-accent bg-accent text-white"
                    : "border-border bg-subtle text-secondary hover:bg-subtle/70"
                )}
              >
                {dept.name}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================
// Department Assignment Dialog
// ============================================================

function DepartmentAssignDialog({
  user,
  onClose,
}: {
  user: User;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [selectedDepts, setSelectedDepts] = useState<Set<string>>(new Set());
  const [initialDepts, setInitialDepts] = useState<Set<string>>(new Set());
  const [loaded, setLoaded] = useState(false);

  const { data: departments } = useQuery({
    queryKey: ["departments"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/departments", {});
      if (error || !data) return [] as Department[];
      return data as unknown as Department[];
    },
  });

  const userDeptsQuery = useQuery({
    queryKey: ["admin", "users", user.id, "departments"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/users/{user_id}/departments", {
        params: { path: { user_id: user.id } },
      });
      if (error || !data) return [] as UserDepartment[];
      return data as unknown as UserDepartment[];
    },
  });

  // Initialize selected departments once loaded
  if (userDeptsQuery.data && !loaded) {
    const ids = new Set(userDeptsQuery.data.map((d) => d.id));
    setSelectedDepts(ids);
    setInitialDepts(ids);
    setLoaded(true);
  }

  const saveMutation = useMutation({
    mutationFn: async () => {
      const toAdd = [...selectedDepts].filter((id) => !initialDepts.has(id));
      const toRemove = [...initialDepts].filter((id) => !selectedDepts.has(id));
      await Promise.all([
        ...toAdd.map((deptId) =>
          apiClient.POST("/admin/departments/{department_id}/members", {
            params: { path: { department_id: deptId } },
            body: { user_id: user.id, role: "member" } as never,
          }),
        ),
        ...toRemove.map((deptId) =>
          apiClient.DELETE("/admin/departments/{department_id}/members/{user_id}", {
            params: { path: { department_id: deptId, user_id: user.id } },
          }),
        ),
      ]);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "users", user.id, "departments"] });
      toast.show({ title: "部门分配已更新", variant: "success" });
      onClose();
    },
    onError: () => {
      toast.show({ title: "更新失败", variant: "danger" });
    },
  });

  const toggleDept = (deptId: string) => {
    setSelectedDepts((prev) => {
      const next = new Set(prev);
      if (next.has(deptId)) {
        next.delete(deptId);
      } else {
        next.add(deptId);
      }
      return next;
    });
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>分配部门 — {user.name}</DialogTitle>
        </DialogHeader>
        <div className="py-2">
          {userDeptsQuery.isLoading ? (
            <div className="flex h-20 items-center justify-center"><Spinner /></div>
          ) : departments && departments.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {departments.map((dept) => (
                <button
                  key={dept.id}
                  type="button"
                  onClick={() => toggleDept(dept.id)}
                  className={cn(
                    "rounded-md border px-2.5 py-1 text-xs transition-colors",
                    selectedDepts.has(dept.id)
                      ? "border-accent bg-accent text-white"
                      : "border-border bg-subtle text-secondary hover:bg-subtle/70"
                  )}
                >
                  {dept.name}
                </button>
              ))}
            </div>
          ) : (
            <p className="text-sm text-tertiary">暂无部门，请先创建部门</p>
          )}
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">取消</Button>
          </DialogClose>
          <Button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending}>
            {saveMutation.isPending ? "保存中..." : "保存"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
