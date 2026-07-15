import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import { BackendUrlSettings, PreferencesForm, Spinner } from "@eaos/shared";
import { User, Mail, Shield } from "lucide-react";

interface MeResponse {
  id: string;
  tenant_id: string;
  email: string;
  name: string;
  role: string;
  status: string;
}

const ROLE_LABELS: Record<string, string> = {
  admin: "管理员",
  manager: "经理",
  employee: "员工",
};

export default function SettingsPage() {
  const query = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/me", {});
      if (error || !data) return null;
      return data as unknown as MeResponse;
    },
  });

  if (query.isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  }

  const me = query.data;
  if (!me) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-secondary">
        加载失败
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <h1 className="text-2xl font-semibold text-foreground">个人设置</h1>
        <p className="mt-1 text-sm text-secondary">管理你的个人资料和偏好</p>
      </div>

      <div className="flex-1 overflow-y-auto px-8 py-6">
        <div className="mx-auto max-w-2xl space-y-6">
          {/* Profile Card */}
          <section className="rounded-md border border-border bg-elevated p-6 shadow-sm">
            <h2 className="text-lg font-medium text-foreground">个人资料</h2>
            <div className="mt-4 space-y-4">
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-accent-subtle text-accent">
                  <User className="h-5 w-5" strokeWidth={1.75} />
                </div>
                <div>
                  <p className="text-sm font-medium text-foreground">{me.name}</p>
                  <p className="text-xs text-tertiary">{me.id}</p>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-subtle text-secondary">
                  <Mail className="h-5 w-5" strokeWidth={1.75} />
                </div>
                <div>
                  <p className="text-sm font-medium text-foreground">{me.email}</p>
                  <p className="text-xs text-tertiary">邮箱</p>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <div className="flex h-10 w-10 items-center justify-center rounded-full bg-subtle text-secondary">
                  <Shield className="h-5 w-5" strokeWidth={1.75} />
                </div>
                <div>
                  <p className="text-sm font-medium text-foreground">
                    {ROLE_LABELS[me.role] ?? me.role}
                  </p>
                  <p className="text-xs text-tertiary">角色</p>
                </div>
              </div>
            </div>
          </section>

          {/* Structured preferences (theme, default agent, notifications) */}
          <PreferencesForm showDefaultAgent />

          {/* Backend Connection — required for desktop mode */}
          <BackendUrlSettings />
        </div>
      </div>
    </div>
  );
}
