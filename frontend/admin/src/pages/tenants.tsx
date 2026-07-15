import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import {
  Button,
  Input,
  Spinner,
  Badge,
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
  EmptyState,
  toast,
  useTranslation,
} from "@eaos/shared";
import {
  Globe2,
  Plus,
  Pencil,
  Trash2,
  Power,
  PowerOff,
  BarChart3,
  Gauge,
} from "lucide-react";

interface Tenant {
  id: string;
  name: string;
  slug: string;
  status: string;
  plan: string;
  settings: Record<string, unknown>;
  created_at: string;
  updated_at?: string;
}

interface TenantStats {
  tenant_id: string;
  stats: {
    users: number;
    departments: number;
    agents: number;
    sessions: number;
    skills: number;
    documents: number;
    memories: number;
  };
}

interface Quota {
  id: string;
  scope: string;
  owner_id: string | null;
  period: string;
  token_limit: number;
  token_used: number;
  cost_limit_usd: number | null;
  cost_used_usd: number;
  reset_at: string | null;
}

const PLAN_LABELS: Record<string, string> = {
  standard: "Standard",
  enterprise: "Enterprise",
  starter: "Starter",
};

const STATUS_VARIANT: Record<string, "default" | "secondary" | "danger"> = {
  active: "default",
  suspended: "secondary",
  deleted: "danger",
};

export default function TenantsPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<Tenant | null>(null);
  const [deleting, setDeleting] = useState<Tenant | null>(null);
  const [statsTenant, setStatsTenant] = useState<Tenant | null>(null);
  const [quotaTenant, setQuotaTenant] = useState<Tenant | null>(null);
  const [createForm, setCreateForm] = useState({ name: "", slug: "", region: "", plan: "standard" });
  const [editForm, setEditForm] = useState({ name: "", plan: "", status: "" });

  const listQuery = useQuery({
    queryKey: ["super", "tenants"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/super/tenants", {});
      if (error || !data) return [] as Tenant[];
      return data as unknown as Tenant[];
    },
  });

  const createMutation = useMutation({
    mutationFn: async (input: typeof createForm) => {
      const { data, error } = await apiClient.POST("/super/tenants", {
        body: {
          name: input.name,
          slug: input.slug,
          plan: input.plan,
          settings: input.region ? { region: input.region } : {},
        },
      });
      if (error) throw error;
      return data;
    },
    onSuccess: () => {
      toast.show({ title: t("tenants.toastCreated") });
      setShowCreate(false);
      setCreateForm({ name: "", slug: "", region: "", plan: "standard" });
      void queryClient.invalidateQueries({ queryKey: ["super", "tenants"] });
    },
    onError: () => toast.show({ title: t("tenants.toastFailed"), variant: "danger" }),
  });

  const updateMutation = useMutation({
    mutationFn: async (input: { id: string; name: string; plan: string; status: string }) => {
      const { data, error } = await apiClient.PATCH("/super/tenants/{tenant_id}", {
        params: { path: { tenant_id: input.id } },
        body: {
          name: input.name,
          status: input.status,
          plan: input.plan,
        },
      });
      if (error) throw error;
      return data;
    },
    onSuccess: () => {
      toast.show({ title: t("tenants.toastUpdated") });
      setEditing(null);
      void queryClient.invalidateQueries({ queryKey: ["super", "tenants"] });
    },
    onError: () => toast.show({ title: t("tenants.toastFailed"), variant: "danger" }),
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      const { error } = await apiClient.DELETE("/super/tenants/{tenant_id}", {
        params: { path: { tenant_id: id } },
      });
      if (error) throw error;
    },
    onSuccess: () => {
      toast.show({ title: t("tenants.toastDeleted") });
      setDeleting(null);
      void queryClient.invalidateQueries({ queryKey: ["super", "tenants"] });
    },
    onError: () => toast.show({ title: t("tenants.toastFailed"), variant: "danger" }),
  });

  const enableMutation = useMutation({
    mutationFn: async (id: string) => {
      const { error } = await apiClient.POST("/super/tenants/{tenant_id}/enable", {
        params: { path: { tenant_id: id } },
      });
      if (error) throw error;
    },
    onSuccess: () => {
      toast.show({ title: t("tenants.toastEnabled") });
      void queryClient.invalidateQueries({ queryKey: ["super", "tenants"] });
    },
    onError: () => toast.show({ title: t("tenants.toastFailed"), variant: "danger" }),
  });

  const disableMutation = useMutation({
    mutationFn: async (id: string) => {
      const { error } = await apiClient.POST("/super/tenants/{tenant_id}/disable", {
        params: { path: { tenant_id: id } },
      });
      if (error) throw error;
    },
    onSuccess: () => {
      toast.show({ title: t("tenants.toastDisabled") });
      void queryClient.invalidateQueries({ queryKey: ["super", "tenants"] });
    },
    onError: () => toast.show({ title: t("tenants.toastFailed"), variant: "danger" }),
  });

  function openEdit(tenant: Tenant) {
    setEditForm({ name: tenant.name, plan: tenant.plan, status: tenant.status });
    setEditing(tenant);
  }

  if (listQuery.isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  }

  const tenants = listQuery.data ?? [];

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-6">
      <header className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{t("tenants.title")}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t("tenants.subtitle")}</p>
        </div>
        <Button onClick={() => setShowCreate(true)}>
          <Plus className="mr-1.5 h-4 w-4" />
          {t("tenants.create")}
        </Button>
      </header>

      {tenants.length === 0 ? (
        <EmptyState
          icon={Globe2}
          title={t("tenants.emptyTitle")}
          description={t("tenants.emptyDesc")}
          action={
            <Button onClick={() => setShowCreate(true)}>
              <Plus className="mr-1.5 h-4 w-4" />
              {t("tenants.create")}
            </Button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {tenants.map((tenant) => (
            <Card key={tenant.id} className="flex flex-col">
              <CardHeader>
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-3">
                    <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                      <Globe2 className="h-5 w-5" />
                    </div>
                    <div>
                      <CardTitle className="text-base">{tenant.name}</CardTitle>
                      <CardDescription className="text-xs">{tenant.slug}</CardDescription>
                    </div>
                  </div>
                  <Badge variant={STATUS_VARIANT[tenant.status] ?? "secondary"}>
                    {tenant.status}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="flex-1 space-y-3">
                <dl className="grid grid-cols-2 gap-2 text-xs">
                  <div>
                    <dt className="text-muted-foreground">{t("tenants.labelPlan")}</dt>
                    <dd className="font-medium">{PLAN_LABELS[tenant.plan] ?? tenant.plan}</dd>
                  </div>
                  <div>
                    <dt className="text-muted-foreground">{t("tenants.labelCreatedAt")}</dt>
                    <dd className="font-medium">
                      {new Date(tenant.created_at).toLocaleDateString()}
                    </dd>
                  </div>
                </dl>
                <div className="flex flex-wrap gap-1.5 pt-1">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setStatsTenant(tenant)}
                  >
                    <BarChart3 className="mr-1 h-3.5 w-3.5" />
                    {t("tenants.viewStats")}
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setQuotaTenant(tenant)}
                  >
                    <Gauge className="mr-1 h-3.5 w-3.5" />
                    {t("tenants.manageQuotas")}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => openEdit(tenant)}
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </Button>
                  {tenant.status === "active" ? (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => disableMutation.mutate(tenant.id)}
                      disabled={disableMutation.isPending}
                    >
                      <PowerOff className="h-3.5 w-3.5" />
                    </Button>
                  ) : (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => enableMutation.mutate(tenant.id)}
                      disabled={enableMutation.isPending}
                    >
                      <Power className="h-3.5 w-3.5" />
                    </Button>
                  )}
                  <Button
                    size="sm"
                    variant="ghost"
                    className="text-danger hover:text-danger"
                    onClick={() => setDeleting(tenant)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Create dialog */}
      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("tenants.create")}</DialogTitle>
            <DialogDescription>{t("tenants.subtitle")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs text-muted-foreground">{t("tenants.labelName")}</label>
              <Input
                value={createForm.name}
                onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })}
                placeholder={t("tenants.placeholderName")}
              />
            </div>
            <div>
              <label className="text-xs text-muted-foreground">{t("tenants.labelSlug")}</label>
              <Input
                value={createForm.slug}
                onChange={(e) => setCreateForm({ ...createForm, slug: e.target.value })}
                placeholder={t("tenants.placeholderSlug")}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-muted-foreground">{t("tenants.labelPlan")}</label>
                <select
                  className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  value={createForm.plan}
                  onChange={(e) => setCreateForm({ ...createForm, plan: e.target.value })}
                >
                  <option value="starter">Starter</option>
                  <option value="standard">Standard</option>
                  <option value="enterprise">Enterprise</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">{t("tenants.labelSettings")}</label>
                <Input
                  value={createForm.region}
                  onChange={(e) => setCreateForm({ ...createForm, region: e.target.value })}
                  placeholder={t("tenants.placeholderRegion")}
                />
              </div>
            </div>
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">Cancel</Button>
            </DialogClose>
            <Button
              onClick={() => createMutation.mutate(createForm)}
              disabled={!createForm.name || !createForm.slug || createMutation.isPending}
            >
              {createMutation.isPending ? <Spinner /> : t("tenants.create")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit dialog */}
      <Dialog open={!!editing} onOpenChange={(o) => !o && setEditing(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("tenants.edit")}</DialogTitle>
            <DialogDescription>{editing?.slug}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-xs text-muted-foreground">{t("tenants.labelName")}</label>
              <Input
                value={editForm.name}
                onChange={(e) => setEditForm({ ...editForm, name: e.target.value })}
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-muted-foreground">{t("tenants.labelPlan")}</label>
                <select
                  className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  value={editForm.plan}
                  onChange={(e) => setEditForm({ ...editForm, plan: e.target.value })}
                >
                  <option value="starter">Starter</option>
                  <option value="standard">Standard</option>
                  <option value="enterprise">Enterprise</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">{t("tenants.labelStatus")}</label>
                <select
                  className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  value={editForm.status}
                  onChange={(e) => setEditForm({ ...editForm, status: e.target.value })}
                >
                  <option value="active">Active</option>
                  <option value="suspended">Suspended</option>
                  <option value="deleted">Deleted</option>
                </select>
              </div>
            </div>
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">Cancel</Button>
            </DialogClose>
            <Button
              onClick={() =>
                editing &&
                updateMutation.mutate({
                  id: editing.id,
                  name: editForm.name,
                  plan: editForm.plan,
                  status: editForm.status,
                })
              }
              disabled={updateMutation.isPending}
            >
              {updateMutation.isPending ? <Spinner /> : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirm */}
      <Dialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("tenants.delete")}</DialogTitle>
            <DialogDescription>{t("tenants.confirmDelete")}</DialogDescription>
          </DialogHeader>
          <div className="rounded-md border border-danger/30 bg-danger/5 p-3 text-sm">
            <div className="font-medium">{deleting?.name}</div>
            <div className="text-xs text-muted-foreground">{deleting?.slug}</div>
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">Cancel</Button>
            </DialogClose>
            <Button
              variant="danger"
              onClick={() => deleting && deleteMutation.mutate(deleting.id)}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? <Spinner /> : t("tenants.delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Stats dialog */}
      <StatsDialog tenant={statsTenant} onClose={() => setStatsTenant(null)} />

      {/* Quotas dialog */}
      <QuotasDialog tenant={quotaTenant} onClose={() => setQuotaTenant(null)} />
    </div>
  );
}

function StatsDialog({ tenant, onClose }: { tenant: Tenant | null; onClose: () => void }) {
  const { t } = useTranslation();
  const query = useQuery({
    queryKey: ["super", "tenants", tenant?.id, "stats"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/super/tenants/{tenant_id}/stats", {
        params: { path: { tenant_id: tenant!.id } },
      });
      if (error || !data) return null as TenantStats | null;
      return data as unknown as TenantStats;
    },
    enabled: !!tenant,
  });

  const stats = query.data?.stats;

  return (
    <Dialog open={!!tenant} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("tenants.viewStats")} · {tenant?.name}</DialogTitle>
          <DialogDescription>{tenant?.slug}</DialogDescription>
        </DialogHeader>
        {query.isLoading ? (
          <div className="flex justify-center py-6"><Spinner /></div>
        ) : stats ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {(Object.keys(stats) as Array<keyof typeof stats>).map((key) => (
              <div key={key} className="rounded-lg border bg-card p-3">
                <div className="text-xs text-muted-foreground">{t(`tenants.stats.${key}`)}</div>
                <div className="mt-1 text-xl font-semibold">{stats[key].toLocaleString()}</div>
              </div>
            ))}
          </div>
        ) : (
          <div className="py-6 text-center text-sm text-muted-foreground">No data</div>
        )}
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Close</Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function QuotasDialog({ tenant, onClose }: { tenant: Tenant | null; onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Array<{ period: string; token_limit: number; cost_limit_usd: number | null }>>([]);

  const query = useQuery({
    queryKey: ["super", "tenants", tenant?.id, "quotas"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/super/tenants/{tenant_id}/quotas", {
        params: { path: { tenant_id: tenant!.id } },
      });
      if (error || !data) return [] as Quota[];
      return data as unknown as Quota[];
    },
    enabled: !!tenant,
    refetchOnWindowFocus: false,
  });

  function initDraft(existing: Quota[]) {
    if (existing.length === 0) {
      return [{ period: "monthly", token_limit: 1000000, cost_limit_usd: 50.0 }];
    }
    return existing.map((q) => ({
      period: q.period,
      token_limit: q.token_limit,
      cost_limit_usd: q.cost_limit_usd,
    }));
  }

  const saveMutation = useMutation({
    mutationFn: async (quotas: typeof draft) => {
      const { error } = await apiClient.PUT("/super/tenants/{tenant_id}/quotas", {
        params: { path: { tenant_id: tenant!.id } },
        body: {
          quotas: quotas.map((q) => ({
            scope: "company",
            period: q.period,
            token_limit: q.token_limit,
            cost_limit_usd: q.cost_limit_usd,
          })),
        },
      });
      if (error) throw error;
    },
    onSuccess: () => {
      toast.show({ title: t("tenants.toastQuotasUpdated") });
      void queryClient.invalidateQueries({ queryKey: ["super", "tenants", tenant?.id, "quotas"] });
      onClose();
    },
    onError: () => toast.show({ title: t("tenants.toastFailed"), variant: "danger" }),
  });

  const existing = query.data ?? [];
  const draftReady = draft.length > 0 || existing.length > 0;

  return (
    <Dialog open={!!tenant} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("tenants.quotas.title")} · {tenant?.name}</DialogTitle>
          <DialogDescription>{tenant?.slug}</DialogDescription>
        </DialogHeader>

        {query.isLoading ? (
          <div className="flex justify-center py-6"><Spinner /></div>
        ) : (
          <div className="space-y-3">
            {/* Existing quotas (read-only display) */}
            {existing.length > 0 && draft.length === 0 && (
              <div className="space-y-2">
                {existing.map((q) => (
                  <div key={q.id} className="rounded-md border bg-card p-3 text-sm">
                    <div className="flex items-center justify-between">
                      <span className="font-medium">
                        {t(`tenants.quotas.${q.period === "monthly" ? "monthly" : "daily"}`)}
                      </span>
                      <Badge variant="secondary">{q.scope}</Badge>
                    </div>
                    <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                      <dt className="text-muted-foreground">{t("tenants.quotas.tokenLimit")}</dt>
                      <dd>{q.token_limit.toLocaleString()}</dd>
                      <dt className="text-muted-foreground">{t("tenants.quotas.tokenUsed")}</dt>
                      <dd>{q.token_used.toLocaleString()}</dd>
                      {q.cost_limit_usd !== null && (
                        <>
                          <dt className="text-muted-foreground">{t("tenants.quotas.costLimit")}</dt>
                          <dd>${q.cost_limit_usd.toFixed(2)}</dd>
                        </>
                      )}
                      <dt className="text-muted-foreground">{t("tenants.quotas.costUsed")}</dt>
                      <dd>${q.cost_used_usd.toFixed(4)}</dd>
                      {q.reset_at && (
                        <>
                          <dt className="text-muted-foreground">{t("tenants.quotas.resetAt")}</dt>
                          <dd>{new Date(q.reset_at).toLocaleString()}</dd>
                        </>
                      )}
                    </dl>
                  </div>
                ))}
              </div>
            )}

            {/* Draft editor */}
            {draft.length > 0 && (
              <div className="space-y-2">
                {draft.map((q, idx) => (
                  <div key={idx} className="grid grid-cols-[120px_1fr_1fr_auto] items-end gap-2">
                    <div>
                      <label className="text-xs text-muted-foreground">{t("tenants.quotas.period")}</label>
                      <select
                        className="flex h-9 w-full rounded-md border border-input bg-transparent px-2 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                        value={q.period}
                        onChange={(e) => {
                          const next = [...draft];
                          next[idx] = { ...q, period: e.target.value };
                          setDraft(next);
                        }}
                      >
                        <option value="monthly">{t("tenants.quotas.monthly")}</option>
                        <option value="daily">{t("tenants.quotas.daily")}</option>
                      </select>
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground">{t("tenants.quotas.tokenLimit")}</label>
                      <Input
                        type="number"
                        value={q.token_limit}
                        onChange={(e) => {
                          const next = [...draft];
                          next[idx] = { ...q, token_limit: Number(e.target.value) };
                          setDraft(next);
                        }}
                      />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground">{t("tenants.quotas.costLimit")}</label>
                      <Input
                        type="number"
                        step="0.01"
                        value={q.cost_limit_usd ?? 0}
                        onChange={(e) => {
                          const next = [...draft];
                          next[idx] = { ...q, cost_limit_usd: Number(e.target.value) };
                          setDraft(next);
                        }}
                      />
                    </div>
                    <Button
                      size="icon"
                      variant="ghost"
                      className="text-danger"
                      onClick={() => setDraft(draft.filter((_, i) => i !== idx))}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                ))}
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() =>
                    setDraft([...draft, { period: "monthly", token_limit: 1000000, cost_limit_usd: 50.0 }])
                  }
                >
                  <Plus className="mr-1 h-3.5 w-3.5" />
                  {t("tenants.quotas.addQuota")}
                </Button>
              </div>
            )}

            {draft.length === 0 && existing.length === 0 && (
              <div className="py-4 text-center text-sm text-muted-foreground">No quotas set</div>
            )}
          </div>
        )}

        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Cancel</Button>
          </DialogClose>
          {draft.length === 0 ? (
            <Button
              onClick={() => setDraft(initDraft(existing))}
              disabled={!draftReady && existing.length === 0}
            >
              {t("tenants.quotas.addQuota")}
            </Button>
          ) : (
            <Button
              onClick={() => saveMutation.mutate(draft)}
              disabled={saveMutation.isPending}
            >
              {saveMutation.isPending ? <Spinner /> : t("tenants.quotas.save")}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
