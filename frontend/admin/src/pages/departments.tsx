import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import { Button, Spinner, cn, toast } from "@eaos/shared";
import { Building2, Plus, Trash2, UserPlus, X } from "lucide-react";

interface Department {
  id: string;
  name: string;
  parent_id: string | null;
  created_at: string;
}

interface Member {
  user_id: string;
  department_id: string;
  role: string;
  joined_at: string;
  name: string;
  email: string;
}

interface DepartmentDetail extends Department {
  members: Member[];
}

interface User {
  id: string;
  name: string;
  email: string;
  role: string;
}

export default function DepartmentsPage() {
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [selectedDept, setSelectedDept] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["admin", "departments"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/departments", {});
      if (error || !data) return [] as Department[];
      return data as unknown as Department[];
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiClient.DELETE("/admin/departments/{department_id}", {
        params: { path: { department_id: id } },
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin", "departments"] });
      toast.show({ title: "部门已删除", variant: "success" });
    },
  });

  const departments = query.data ?? [];

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">部门管理</h1>
            <p className="mt-1 text-sm text-secondary">
              管理组织架构与部门成员，用于技能/记忆/知识的部门级权限
            </p>
          </div>
          <Button onClick={() => setShowCreate(true)}>
            <Plus className="h-4 w-4" />
            新建部门
          </Button>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Department list */}
        <div className="w-72 shrink-0 overflow-y-auto border-r border-border-subtle px-4 py-4">
          {query.isLoading ? (
            <div className="flex h-20 items-center justify-center">
              <Spinner />
            </div>
          ) : departments.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm text-tertiary">
              暂无部门
            </div>
          ) : (
            <div className="space-y-1">
              {departments.map((dept) => (
                <button
                  key={dept.id}
                  type="button"
                  onClick={() => setSelectedDept(dept.id)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm transition-colors",
                    selectedDept === dept.id
                      ? "bg-accent-subtle text-accent"
                      : "text-secondary hover:bg-subtle hover:text-foreground"
                  )}
                >
                  <Building2 className="h-4 w-4 shrink-0" strokeWidth={1.75} />
                  <span className="truncate">{dept.name}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Detail panel */}
        <div className="flex-1 overflow-y-auto px-8 py-6">
          {selectedDept ? (
            <DepartmentDetailPanel
              deptId={selectedDept}
              onDelete={() => {
                deleteMutation.mutate(selectedDept);
                setSelectedDept(null);
              }}
            />
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-tertiary">
              选择左侧部门查看详情
            </div>
          )}
        </div>
      </div>

      {showCreate && (
        <CreateDepartmentModal
          departments={departments}
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            setShowCreate(false);
            void queryClient.invalidateQueries({ queryKey: ["admin", "departments"] });
          }}
        />
      )}
    </div>
  );
}

function DepartmentDetailPanel({
  deptId,
  onDelete,
}: {
  deptId: string;
  onDelete: () => void;
}) {
  const queryClient = useQueryClient();
  const [showAddMember, setShowAddMember] = useState(false);

  const detailQuery = useQuery({
    queryKey: ["admin", "departments", deptId],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/admin/departments/{department_id}",
        { params: { path: { department_id: deptId } } }
      );
      if (error || !data) return null;
      return data as unknown as DepartmentDetail;
    },
  });

  const removeMemberMutation = useMutation({
    mutationFn: async (userId: string) => {
      await apiClient.DELETE(
        "/admin/departments/{department_id}/members/{user_id}",
        {
          params: { path: { department_id: deptId, user_id: userId } },
        }
      );
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["admin", "departments", deptId],
      });
      toast.show({ title: "成员已移除", variant: "success" });
    },
  });

  if (detailQuery.isLoading) {
    return (
      <div className="flex h-20 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  const detail = detailQuery.data;
  if (!detail) {
    return <div className="text-sm text-tertiary">加载失败</div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-foreground">{detail.name}</h2>
          <p className="mt-0.5 text-xs text-tertiary">
            {detail.members.length} 名成员
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="ghost" size="sm" onClick={() => setShowAddMember(true)}>
            <UserPlus className="h-3.5 w-3.5" />
            添加成员
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={onDelete}
            className="text-danger hover:text-danger"
          >
            <Trash2 className="h-3.5 w-3.5" />
            删除部门
          </Button>
        </div>
      </div>

      <div>
        <h3 className="mb-2 text-sm font-medium text-foreground">成员列表</h3>
        {detail.members.length === 0 ? (
          <p className="text-sm text-tertiary">暂无成员</p>
        ) : (
          <div className="space-y-2">
            {detail.members.map((m) => (
              <div
                key={m.user_id}
                className="flex items-center justify-between rounded-md border border-border bg-elevated px-4 py-2.5"
              >
                <div className="flex items-center gap-3">
                  <div className="flex h-8 w-8 items-center justify-center rounded-full bg-accent-subtle text-xs font-medium text-accent">
                    {m.name.charAt(0)}
                  </div>
                  <div>
                    <p className="text-sm font-medium text-foreground">{m.name}</p>
                    <p className="text-xs text-tertiary">{m.email}</p>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <span className="rounded-full bg-subtle px-2 py-0.5 text-xs text-tertiary">
                    {m.role === "manager" ? "管理员" : "成员"}
                  </span>
                  <button
                    type="button"
                    onClick={() => removeMemberMutation.mutate(m.user_id)}
                    className="text-tertiary hover:text-danger"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {showAddMember && (
        <AddMemberModal
          deptId={deptId}
          existingMemberIds={detail.members.map((m) => m.user_id)}
          onClose={() => setShowAddMember(false)}
          onAdded={() => {
            setShowAddMember(false);
            void queryClient.invalidateQueries({
              queryKey: ["admin", "departments", deptId],
            });
          }}
        />
      )}
    </div>
  );
}

function CreateDepartmentModal({
  departments,
  onClose,
  onCreated,
}: {
  departments: Department[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [parentId, setParentId] = useState("");

  const mutation = useMutation({
    mutationFn: async () => {
      await apiClient.POST("/admin/departments", {
        body: {
          name: name.trim(),
          parent_id: parentId || undefined,
        },
      });
    },
    onSuccess: () => {
      toast.show({ title: "部门已创建", variant: "success" });
      onCreated();
    },
    onError: () => {
      toast.show({ title: "创建失败", variant: "danger" });
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-lg bg-elevated p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-foreground">新建部门</h2>
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
            mutation.mutate();
          }}
          className="space-y-3"
        >
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary">
              部门名称
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="例如：研发部"
              required
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-tertiary focus:border-accent focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary">
              上级部门（可选）
            </label>
            <select
              value={parentId}
              onChange={(e) => setParentId(e.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none"
            >
              <option value="">无（顶级部门）</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" type="button" onClick={onClose}>
              取消
            </Button>
            <Button type="submit" disabled={mutation.isPending || !name.trim()}>
              {mutation.isPending ? "创建中..." : "创建"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

function AddMemberModal({
  deptId,
  existingMemberIds,
  onClose,
  onAdded,
}: {
  deptId: string;
  existingMemberIds: string[];
  onClose: () => void;
  onAdded: () => void;
}) {
  const [selectedUserId, setSelectedUserId] = useState("");

  const usersQuery = useQuery({
    queryKey: ["admin", "users"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/users", {});
      if (error || !data) return [] as User[];
      return (data as unknown as { items?: User[] } | User[]).hasOwnProperty("items")
        ? (data as unknown as { items: User[] }).items
        : (data as unknown as User[]);
    },
  });

  const addMutation = useMutation({
    mutationFn: async () => {
      await apiClient.POST("/admin/departments/{department_id}/members", {
        params: { path: { department_id: deptId } },
        body: { user_id: selectedUserId, role: "member" },
      });
    },
    onSuccess: () => {
      toast.show({ title: "成员已添加", variant: "success" });
      onAdded();
    },
    onError: () => {
      toast.show({ title: "添加失败", variant: "danger" });
    },
  });

  const availableUsers = (usersQuery.data ?? []).filter(
    (u) => !existingMemberIds.includes(u.id)
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-lg bg-elevated p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-foreground">添加成员</h2>
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
            if (selectedUserId) addMutation.mutate();
          }}
          className="space-y-3"
        >
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary">
              选择用户
            </label>
            {usersQuery.isLoading ? (
              <Spinner />
            ) : availableUsers.length === 0 ? (
              <p className="text-sm text-tertiary">没有可添加的用户</p>
            ) : (
              <select
                value={selectedUserId}
                onChange={(e) => setSelectedUserId(e.target.value)}
                required
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none"
              >
                <option value="">请选择...</option>
                {availableUsers.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.name} ({u.email})
                  </option>
                ))}
              </select>
            )}
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" type="button" onClick={onClose}>
              取消
            </Button>
            <Button
              type="submit"
              disabled={addMutation.isPending || !selectedUserId}
            >
              {addMutation.isPending ? "添加中..." : "添加"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
