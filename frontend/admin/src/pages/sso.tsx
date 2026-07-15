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
  KeyRound,
  Plus,
  Pencil,
  Trash2,
  FlaskConical,
  CheckCircle2,
  XCircle,
} from "lucide-react";

interface SSOConfig {
  id: string;
  tenant_id: string;
  name: string;
  provider_type: "oidc" | "saml" | "ldap";
  provider_key: string;
  config: Record<string, unknown>;
  enabled: boolean;
  jit_provision: boolean;
  default_role: string;
  created_at: string;
  updated_at?: string;
}

interface TestResult {
  ok: boolean;
  detail: string;
}

const PROVIDER_TYPE_VARIANT: Record<string, "default" | "secondary" | "info"> = {
  oidc: "info",
  saml: "default",
  ldap: "secondary",
};

const DEFAULT_OIDC_CONFIG = {
  client_id: "",
  client_secret: "",
  discovery_url: "",
  scope: "openid email profile",
};

const DEFAULT_LDAP_CONFIG = {
  server_url: "",
  base_dn: "",
  bind_dn_template: "",
  use_ssl: true,
  admin_bind_dn: "",
  admin_bind_password: "",
};

const DEFAULT_SAML_CONFIG = {
  idp_metadata: "",
};

export default function SsoPage() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<SSOConfig | null>(null);
  const [deleting, setDeleting] = useState<SSOConfig | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, TestResult>>({});
  const [createForm, setCreateForm] = useState({
    name: "",
    provider_type: "oidc" as "oidc" | "saml" | "ldap",
    provider_key: "",
    enabled: true,
    jit_provision: true,
    default_role: "employee",
    config: { ...DEFAULT_OIDC_CONFIG } as Record<string, unknown>,
  });

  const listQuery = useQuery({
    queryKey: ["admin", "sso-configs"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/sso-configs", {});
      if (error || !data) return [] as SSOConfig[];
      return data as unknown as SSOConfig[];
    },
  });

  const createMutation = useMutation({
    mutationFn: async (input: typeof createForm) => {
      const { data, error } = await apiClient.POST("/admin/sso-configs", {
        body: {
          name: input.name,
          provider_type: input.provider_type,
          provider_key: input.provider_key,
          config: input.config,
          enabled: input.enabled,
          jit_provision: input.jit_provision,
          default_role: input.default_role,
        },
      });
      if (error) throw error;
      return data;
    },
    onSuccess: () => {
      toast.show({ title: t("sso.toastCreated") });
      setShowCreate(false);
      resetCreateForm();
      void queryClient.invalidateQueries({ queryKey: ["admin", "sso-configs"] });
    },
    onError: () => toast.show({ title: t("sso.toastFailed"), variant: "danger" }),
  });

  const updateMutation = useMutation({
    mutationFn: async (input: { id: string; body: Partial<SSOConfig> }) => {
      const { data, error } = await apiClient.PUT("/admin/sso-configs/{config_id}", {
        params: { path: { config_id: input.id } },
        body: input.body,
      });
      if (error) throw error;
      return data;
    },
    onSuccess: () => {
      toast.show({ title: t("sso.toastUpdated") });
      setEditing(null);
      void queryClient.invalidateQueries({ queryKey: ["admin", "sso-configs"] });
    },
    onError: () => toast.show({ title: t("sso.toastFailed"), variant: "danger" }),
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      const { error } = await apiClient.DELETE("/admin/sso-configs/{config_id}", {
        params: { path: { config_id: id } },
      });
      if (error) throw error;
    },
    onSuccess: () => {
      toast.show({ title: t("sso.toastDeleted") });
      setDeleting(null);
      void queryClient.invalidateQueries({ queryKey: ["admin", "sso-configs"] });
    },
    onError: () => toast.show({ title: t("sso.toastFailed"), variant: "danger" }),
  });

  const testMutation = useMutation({
    mutationFn: async (id: string) => {
      const { data, error } = await apiClient.POST("/admin/sso-configs/{config_id}/test", {
        params: { path: { config_id: id } },
      });
      if (error) throw error;
      return data as unknown as TestResult;
    },
    onSuccess: (result, id) => {
      setTestResult((prev) => ({ ...prev, [id]: result }));
      setTestingId(null);
      if (result.ok) {
        toast.show({ title: t("sso.toastTestOk"), description: result.detail });
      } else {
        toast.show({ title: t("sso.toastTestFailed"), description: result.detail, variant: "danger" });
      }
    },
    onError: () => {
      setTestingId(null);
      toast.show({ title: t("sso.toastTestFailed"), variant: "danger" });
    },
  });

  function resetCreateForm() {
    setCreateForm({
      name: "",
      provider_type: "oidc",
      provider_key: "",
      enabled: true,
      jit_provision: true,
      default_role: "employee",
      config: { ...DEFAULT_OIDC_CONFIG },
    });
  }

  function handleProviderTypeChange(type: "oidc" | "saml" | "ldap") {
    let newConfig: Record<string, unknown>;
    if (type === "oidc") newConfig = { ...DEFAULT_OIDC_CONFIG };
    else if (type === "ldap") newConfig = { ...DEFAULT_LDAP_CONFIG };
    else newConfig = { ...DEFAULT_SAML_CONFIG };
    setCreateForm({ ...createForm, provider_type: type, config: newConfig });
  }

  if (listQuery.isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  }

  const configs = listQuery.data ?? [];

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-6">
      <header className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{t("sso.title")}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t("sso.subtitle")}</p>
        </div>
        <Button onClick={() => setShowCreate(true)}>
          <Plus className="mr-1.5 h-4 w-4" />
          {t("sso.create")}
        </Button>
      </header>

      {configs.length === 0 ? (
        <EmptyState
          icon={KeyRound}
          title={t("sso.emptyTitle")}
          description={t("sso.emptyDesc")}
          action={
            <Button onClick={() => setShowCreate(true)}>
              <Plus className="mr-1.5 h-4 w-4" />
              {t("sso.create")}
            </Button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {configs.map((cfg) => {
            const result = testResult[cfg.id];
            return (
              <Card key={cfg.id} className="flex flex-col">
                <CardHeader>
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-3">
                      <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                        <KeyRound className="h-5 w-5" />
                      </div>
                      <div>
                        <CardTitle className="text-base">{cfg.name}</CardTitle>
                        <CardDescription className="text-xs">{cfg.provider_key}</CardDescription>
                      </div>
                    </div>
                    <div className="flex flex-col items-end gap-1">
                      <Badge variant={PROVIDER_TYPE_VARIANT[cfg.provider_type]}>
                        {t(`sso.providerType${cfg.provider_type.charAt(0).toUpperCase()}${cfg.provider_type.slice(1)}`)}
                      </Badge>
                      <Badge variant={cfg.enabled ? "success" : "secondary"}>
                        {cfg.enabled ? "ON" : "OFF"}
                      </Badge>
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="flex-1 space-y-3">
                  <dl className="grid grid-cols-2 gap-2 text-xs">
                    <div>
                      <dt className="text-muted-foreground">{t("sso.labelDefaultRole")}</dt>
                      <dd className="font-medium">{cfg.default_role}</dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground">{t("sso.labelJitProvision")}</dt>
                      <dd className="font-medium">{cfg.jit_provision ? "✓" : "—"}</dd>
                    </div>
                  </dl>
                  {result && (
                    <div
                      className={`flex items-start gap-2 rounded-md p-2 text-xs ${
                        result.ok
                          ? "border border-success/30 bg-success/5 text-success"
                          : "border border-danger/30 bg-danger/5 text-danger"
                      }`}
                    >
                      {result.ok ? (
                        <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
                      ) : (
                        <XCircle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
                      )}
                      <span className="break-all">{result.detail}</span>
                    </div>
                  )}
                  <div className="flex flex-wrap gap-1.5 pt-1">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        setTestingId(cfg.id);
                        testMutation.mutate(cfg.id);
                      }}
                      disabled={testingId === cfg.id}
                    >
                      {testingId === cfg.id ? (
                        <Spinner />
                      ) : (
                        <FlaskConical className="mr-1 h-3.5 w-3.5" />
                      )}
                      {testingId === cfg.id ? t("sso.testRunning") : t("sso.test")}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => setEditing(cfg)}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="text-danger"
                      onClick={() => setDeleting(cfg)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      {/* Create dialog */}
      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{t("sso.create")}</DialogTitle>
            <DialogDescription>{t("sso.subtitle")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-muted-foreground">{t("sso.labelName")}</label>
                <Input
                  value={createForm.name}
                  onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })}
                  placeholder={t("sso.placeholderName")}
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">{t("sso.labelProviderKey")}</label>
                <Input
                  value={createForm.provider_key}
                  onChange={(e) => setCreateForm({ ...createForm, provider_key: e.target.value })}
                  placeholder={t("sso.placeholderKey")}
                />
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="text-xs text-muted-foreground">{t("sso.labelProviderType")}</label>
                <select
                  className="flex h-9 w-full rounded-md border border-input bg-transparent px-2 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  value={createForm.provider_type}
                  onChange={(e) => handleProviderTypeChange(e.target.value as "oidc" | "saml" | "ldap")}
                >
                  <option value="oidc">{t("sso.providerTypeOidc")}</option>
                  <option value="saml">{t("sso.providerTypeSaml")}</option>
                  <option value="ldap">{t("sso.providerTypeLdap")}</option>
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">{t("sso.labelDefaultRole")}</label>
                <select
                  className="flex h-9 w-full rounded-md border border-input bg-transparent px-2 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  value={createForm.default_role}
                  onChange={(e) => setCreateForm({ ...createForm, default_role: e.target.value })}
                >
                  <option value="employee">Employee</option>
                  <option value="manager">Manager</option>
                  <option value="admin">Admin</option>
                  <option value="viewer">Viewer</option>
                </select>
              </div>
              <div className="flex items-end gap-3">
                <label className="flex items-center gap-1.5 text-xs">
                  <input
                    type="checkbox"
                    checked={createForm.enabled}
                    onChange={(e) => setCreateForm({ ...createForm, enabled: e.target.checked })}
                  />
                  {t("sso.labelEnabled")}
                </label>
                <label className="flex items-center gap-1.5 text-xs">
                  <input
                    type="checkbox"
                    checked={createForm.jit_provision}
                    onChange={(e) => setCreateForm({ ...createForm, jit_provision: e.target.checked })}
                  />
                  {t("sso.labelJitProvision")}
                </label>
              </div>
            </div>
            <ProviderConfigEditor
              type={createForm.provider_type}
              config={createForm.config}
              onChange={(config) => setCreateForm({ ...createForm, config })}
            />
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">Cancel</Button>
            </DialogClose>
            <Button
              onClick={() => createMutation.mutate(createForm)}
              disabled={!createForm.name || !createForm.provider_key || createMutation.isPending}
            >
              {createMutation.isPending ? <Spinner /> : t("sso.create")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit dialog */}
      {editing && (
        <EditDialog
          config={editing}
          onClose={() => setEditing(null)}
          onSave={(id, body) => updateMutation.mutate({ id, body })}
          saving={updateMutation.isPending}
        />
      )}

      {/* Delete confirm */}
      <Dialog open={!!deleting} onOpenChange={(o) => !o && setDeleting(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("sso.delete")}</DialogTitle>
            <DialogDescription>{t("sso.confirmDelete")}</DialogDescription>
          </DialogHeader>
          <div className="rounded-md border border-danger/30 bg-danger/5 p-3 text-sm">
            <div className="font-medium">{deleting?.name}</div>
            <div className="text-xs text-muted-foreground">
              {deleting?.provider_type} / {deleting?.provider_key}
            </div>
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
              {deleteMutation.isPending ? <Spinner /> : t("sso.delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function ProviderConfigEditor({
  type,
  config,
  onChange,
}: {
  type: "oidc" | "saml" | "ldap";
  config: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
}) {
  const { t } = useTranslation();

  function update(key: string, value: unknown) {
    onChange({ ...config, [key]: value });
  }

  if (type === "oidc") {
    return (
      <div className="space-y-2 rounded-md border bg-card p-3">
        <div>
          <label className="text-xs text-muted-foreground">{t("sso.oidcFields.client_id")}</label>
          <Input
            value={(config.client_id as string) ?? ""}
            onChange={(e) => update("client_id", e.target.value)}
          />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">{t("sso.oidcFields.client_secret")}</label>
          <Input
            type="password"
            value={(config.client_secret as string) ?? ""}
            onChange={(e) => update("client_secret", e.target.value)}
          />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">{t("sso.oidcFields.discovery_url")}</label>
          <Input
            value={(config.discovery_url as string) ?? ""}
            onChange={(e) => update("discovery_url", e.target.value)}
            placeholder="https://accounts.google.com/.well-known/openid-configuration"
          />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">{t("sso.oidcFields.scope")}</label>
          <Input
            value={(config.scope as string) ?? "openid email profile"}
            onChange={(e) => update("scope", e.target.value)}
          />
        </div>
      </div>
    );
  }

  if (type === "ldap") {
    return (
      <div className="space-y-2 rounded-md border bg-card p-3">
        <div>
          <label className="text-xs text-muted-foreground">{t("sso.ldapFields.server_url")}</label>
          <Input
            value={(config.server_url as string) ?? ""}
            onChange={(e) => update("server_url", e.target.value)}
            placeholder="ldap://corp.example.com:389"
          />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">{t("sso.ldapFields.base_dn")}</label>
          <Input
            value={(config.base_dn as string) ?? ""}
            onChange={(e) => update("base_dn", e.target.value)}
            placeholder="dc=corp,dc=example,dc=com"
          />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">{t("sso.ldapFields.bind_dn_template")}</label>
          <Input
            value={(config.bind_dn_template as string) ?? ""}
            onChange={(e) => update("bind_dn_template", e.target.value)}
            placeholder="{username}@corp.example.com"
          />
        </div>
        <div className="flex items-center gap-2 pt-1">
          <input
            type="checkbox"
            id="ldap-use-ssl"
            checked={(config.use_ssl as boolean) ?? true}
            onChange={(e) => update("use_ssl", e.target.checked)}
          />
          <label htmlFor="ldap-use-ssl" className="text-xs">
            {t("sso.ldapFields.use_ssl")}
          </label>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2 rounded-md border bg-card p-3">
      <div>
        <label className="text-xs text-muted-foreground">{t("sso.samlFields.idp_metadata")}</label>
        <textarea
          className="flex min-h-[120px] w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          value={(config.idp_metadata as string) ?? ""}
          onChange={(e) => update("idp_metadata", e.target.value)}
          placeholder="<EntityDescriptor xmlns=..."
        />
      </div>
    </div>
  );
}

function EditDialog({
  config,
  onClose,
  onSave,
  saving,
}: {
  config: SSOConfig;
  onClose: () => void;
  onSave: (id: string, body: Partial<SSOConfig>) => void;
  saving: boolean;
}) {
  const { t } = useTranslation();
  const [form, setForm] = useState({
    name: config.name,
    default_role: config.default_role,
    enabled: config.enabled,
    jit_provision: config.jit_provision,
    config: { ...config.config },
  });

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("sso.edit")} · {config.name}</DialogTitle>
          <DialogDescription>{config.provider_type} / {config.provider_key}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-muted-foreground">{t("sso.labelName")}</label>
              <Input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </div>
            <div>
              <label className="text-xs text-muted-foreground">{t("sso.labelDefaultRole")}</label>
              <select
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-2 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                value={form.default_role}
                onChange={(e) => setForm({ ...form, default_role: e.target.value })}
              >
                <option value="employee">Employee</option>
                <option value="manager">Manager</option>
                <option value="admin">Admin</option>
                <option value="viewer">Viewer</option>
              </select>
            </div>
          </div>
          <div className="flex gap-4">
            <label className="flex items-center gap-1.5 text-xs">
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
              />
              {t("sso.labelEnabled")}
            </label>
            <label className="flex items-center gap-1.5 text-xs">
              <input
                type="checkbox"
                checked={form.jit_provision}
                onChange={(e) => setForm({ ...form, jit_provision: e.target.checked })}
              />
              {t("sso.labelJitProvision")}
            </label>
          </div>
          <ProviderConfigEditor
            type={config.provider_type}
            config={form.config}
            onChange={(cfg) => setForm({ ...form, config: cfg })}
          />
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="outline">Cancel</Button>
          </DialogClose>
          <Button
            onClick={() => onSave(config.id, form)}
            disabled={saving}
          >
            {saving ? <Spinner /> : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
