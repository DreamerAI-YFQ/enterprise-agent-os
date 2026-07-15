import { useState, useEffect, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import { Button, Spinner, toast, cn } from "@eaos/shared";
import { useTranslation } from "@eaos/shared";
import { Shield, Save, RotateCcw, Lock } from "lucide-react";

interface RoleInfo {
  role: string;
  label: string;
  description: string;
}

interface CatalogResource {
  resource: string;
  label: string;
  actions: string[];
}

interface CatalogGroup {
  group: string;
  resources: CatalogResource[];
}

interface PermissionEntry {
  resource: string;
  action: string;
  constraint: Record<string, unknown> | null;
}

type PermissionMap = Map<string, PermissionEntry>;

const ALL_ACTIONS = new Set<string>();

function key(resource: string, action: string): string {
  return `${resource}:${action}`;
}

export default function RolesPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [selectedRole, setSelectedRole] = useState<string>("employee");
  const [draft, setDraft] = useState<PermissionMap>(new Map());
  const [dirty, setDirty] = useState(false);

  const rolesQuery = useQuery({
    queryKey: ["roles"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/roles", {});
      if (error || !data) return [] as RoleInfo[];
      return data as unknown as RoleInfo[];
    },
  });

  const catalogQuery = useQuery({
    queryKey: ["permission-catalog"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/permissions/catalog", {});
      if (error || !data) return [] as CatalogGroup[];
      return data as unknown as CatalogGroup[];
    },
  });

  const permissionsQuery = useQuery({
    queryKey: ["role-permissions", selectedRole],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/roles/{role}/permissions", {
        params: { path: { role: selectedRole } },
      });
      if (error || !data) return [] as PermissionEntry[];
      return data as unknown as PermissionEntry[];
    },
    enabled: !!selectedRole,
  });

  useEffect(() => {
    if (permissionsQuery.data) {
      const map = new Map<string, PermissionEntry>();
      for (const p of permissionsQuery.data) {
        map.set(key(p.resource, p.action), p);
      }
      setDraft(map);
      setDirty(false);
    }
  }, [permissionsQuery.data]);

  // Track all known actions for "select all" per resource.
  useMemo(() => {
    for (const group of catalogQuery.data ?? []) {
      for (const res of group.resources) {
        for (const a of res.actions) ALL_ACTIONS.add(a);
      }
    }
  }, [catalogQuery.data]);

  const saveMutation = useMutation({
    mutationFn: async (permissions: PermissionEntry[]) => {
      const { error } = await apiClient.PUT("/admin/roles/{role}/permissions", {
        params: { path: { role: selectedRole } },
        body: { permissions },
      });
      if (error) throw new Error(JSON.stringify(error));
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["role-permissions", selectedRole] });
      void queryClient.invalidateQueries({ queryKey: ["permission-matrix"] });
      toast.show({ title: t("common.saved"), variant: "success" });
      setDirty(false);
    },
    onError: () => {
      toast.show({ title: t("common.error"), variant: "danger" });
    },
  });

  const togglePermission = (resource: string, action: string) => {
    setDraft((prev) => {
      const next = new Map(prev);
      const k = key(resource, action);
      if (next.has(k)) {
        next.delete(k);
      } else {
        next.set(k, { resource, action, constraint: null });
      }
      return next;
    });
    setDirty(true);
  };

  const toggleResourceAll = (resource: string, actions: string[]) => {
    setDraft((prev) => {
      const next = new Map(prev);
      const allOn = actions.every((a) => next.has(key(resource, a)));
      if (allOn) {
        for (const a of actions) next.delete(key(resource, a));
      } else {
        for (const a of actions) {
          if (!next.has(key(resource, a))) {
            next.set(key(resource, a), { resource, action: a, constraint: null });
          }
        }
      }
      return next;
    });
    setDirty(true);
  };

  const handleSave = () => {
    const permissions = Array.from(draft.values());
    saveMutation.mutate(permissions);
  };

  const handleReset = () => {
    if (permissionsQuery.data) {
      const map = new Map<string, PermissionEntry>();
      for (const p of permissionsQuery.data) {
        map.set(key(p.resource, p.action), p);
      }
      setDraft(map);
      setDirty(false);
    }
  };

  if (rolesQuery.isLoading || catalogQuery.isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  }

  const isAdmin = selectedRole === "admin";

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold text-foreground">
            <Shield className="h-5 w-5 text-accent" strokeWidth={1.75} />
            {t("roles.title")}
          </h1>
          <p className="mt-1 text-sm text-tertiary">{t("roles.subtitle")}</p>
        </div>
        {dirty && !isAdmin && (
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" onClick={handleReset}>
              <RotateCcw className="h-3.5 w-3.5" />
              {t("common.cancel")}
            </Button>
            <Button size="sm" onClick={handleSave} disabled={saveMutation.isPending}>
              {saveMutation.isPending ? (
                t("common.loading")
              ) : (
                <>
                  <Save className="h-3.5 w-3.5" />
                  {t("common.save")}
                </>
              )}
            </Button>
          </div>
        )}
      </div>

      {/* Role selector */}
      <div className="flex gap-1 rounded-md border border-border bg-elevated p-1">
        {(rolesQuery.data ?? []).map((r) => (
          <button
            key={r.role}
            onClick={() => setSelectedRole(r.role)}
            className={cn(
              "flex-1 rounded px-3 py-2 text-sm font-medium transition-colors",
              selectedRole === r.role
                ? "bg-accent text-white"
                : "text-secondary hover:bg-subtle hover:text-foreground",
            )}
          >
            {r.label}
          </button>
        ))}
      </div>

      {/* Role description */}
      {(rolesQuery.data ?? [])
        .filter((r) => r.role === selectedRole)
        .map((r) => (
          <p key={r.role} className="text-xs text-tertiary">
            {r.description}
          </p>
        ))}

      {/* Admin notice */}
      {isAdmin && (
        <div className="flex items-center gap-2 rounded-md border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-700 dark:bg-amber-950/30 dark:text-amber-300">
          <Lock className="h-4 w-4 shrink-0" />
          {t("roles.adminNotice")}
        </div>
      )}

      {/* Permission matrix */}
      <div className="space-y-4">
        {(catalogQuery.data ?? []).map((group) => (
          <div
            key={group.group}
            className="rounded-md border border-border bg-elevated"
          >
            <div className="border-b border-border-subtle px-4 py-2.5">
              <h3 className="text-sm font-semibold text-foreground">{group.group}</h3>
            </div>
            <div className="divide-y divide-border-subtle">
              {group.resources.map((res) => {
                const allOn = res.actions.every((a) => draft.has(key(res.resource, a)));
                const someOn = res.actions.some((a) => draft.has(key(res.resource, a)));
                return (
                  <div
                    key={res.resource}
                    className="flex items-center justify-between px-4 py-2.5"
                  >
                    <button
                      type="button"
                      onClick={() => !isAdmin && toggleResourceAll(res.resource, res.actions)}
                      disabled={isAdmin}
                      className="text-sm font-medium text-foreground disabled:cursor-not-allowed"
                    >
                      <span className="mr-2 inline-flex h-4 w-4 items-center justify-center">
                        <span
                          className={cn(
                            "h-3.5 w-3.5 rounded border transition-colors",
                            allOn
                              ? "border-accent bg-accent"
                              : someOn
                                ? "border-accent bg-accent/40"
                                : "border-border bg-elevated",
                          )}
                        >
                          {allOn && (
                            <svg viewBox="0 0 12 12" className="h-full w-full text-white">
                              <path
                                d="M2.5 6L5 8.5L9.5 3.5"
                                stroke="currentColor"
                                strokeWidth="2"
                                fill="none"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                              />
                            </svg>
                          )}
                          {someOn && !allOn && (
                            <svg viewBox="0 0 12 12" className="h-full w-full text-white">
                              <path
                                d="M2.5 6H9.5"
                                stroke="currentColor"
                                strokeWidth="2"
                                strokeLinecap="round"
                              />
                            </svg>
                          )}
                        </span>
                      </span>
                      {res.label}
                    </button>
                    <div className="flex flex-wrap gap-1.5">
                      {res.actions.map((action) => {
                        const checked = draft.has(key(res.resource, action));
                        return (
                          <button
                            key={action}
                            type="button"
                            onClick={() => !isAdmin && togglePermission(res.resource, action)}
                            disabled={isAdmin}
                            className={cn(
                              "rounded px-2 py-0.5 text-xs font-medium transition-colors disabled:cursor-not-allowed",
                              checked
                                ? "bg-accent text-white"
                                : "bg-subtle text-tertiary hover:bg-subtle/70 hover:text-secondary",
                            )}
                          >
                            {action}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
