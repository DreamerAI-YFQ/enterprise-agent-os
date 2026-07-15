import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth, BackendUrlBanner } from "@eaos/shared";
import { apiClient, useAuthStore } from "@eaos/shared/api";
import { Sparkles, Mail, Loader2, KeyRound, LogIn } from "lucide-react";

interface SSOProvider {
  provider_key: string;
  name: string;
  provider_type: "oidc" | "saml" | "ldap";
}

export default function Login() {
  const navigate = useNavigate();
  const { login } = useAuth();
  const setAuth = useAuthStore((s) => s.setAuth);
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [providers, setProviders] = useState<SSOProvider[]>([]);
  const [ldapCreds, setLdapCreds] = useState({ provider_key: "", username: "", password: "" });
  const [ldapLoading, setLdapLoading] = useState(false);
  const [showLdapForm, setShowLdapForm] = useState(false);

  useEffect(() => {
    apiClient
      .GET("/auth/sso/providers", {})
      .then(({ data }) => {
        if (data) setProviders(data as unknown as SSOProvider[]);
      })
      .catch(() => undefined);
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim()) return;
    setLoading(true);
    setError(null);
    try {
      await login(email.trim());
      navigate("/admin", { replace: true });
    } catch {
      setError("登录失败，请检查邮箱是否为管理员账号");
    } finally {
      setLoading(false);
    }
  };

  function handleOidcLogin(providerKey: string) {
    const base = (import.meta as unknown as { env: { VITE_API_BASE_URL?: string } }).env?.VITE_API_BASE_URL || "";
    const root = base.replace(/\/$/, "");
    window.location.href = `${root}/auth/sso/${providerKey}/login`;
  }

  async function handleLdapLogin(e: React.FormEvent) {
    e.preventDefault();
    if (!ldapCreds.provider_key || !ldapCreds.username || !ldapCreds.password) return;
    setLdapLoading(true);
    setError(null);
    try {
      const { data, error } = await apiClient.POST("/auth/sso/ldap/login", {
        body: {
          provider_key: ldapCreds.provider_key,
          username: ldapCreds.username,
          password: ldapCreds.password,
        },
      });
      if (error || !data) {
        throw new Error("LDAP login failed");
      }
      const resp = data as {
        access_token: string;
        user: { id: string; email: string; name?: string; role: string };
      };
      setAuth(resp.access_token, {
        id: resp.user.id,
        tenant_id: "",
        email: resp.user.email,
        name: resp.user.name ?? resp.user.email,
        role: resp.user.role === "admin" || resp.user.role === "super_admin" ? "admin" : "employee",
      });
      // Fetch full profile from /me (includes tenant_id).
      const { data: meData } = await apiClient.GET("/me", {});
      if (meData) {
        useAuthStore.getState().setUser(meData as never);
      }
      navigate("/admin", { replace: true });
    } catch {
      setError("LDAP 登录失败，请检查用户名和密码");
    } finally {
      setLdapLoading(false);
    }
  }

  const oidcProviders = providers.filter((p) => p.provider_type === "oidc" || p.provider_type === "saml");
  const ldapProviders = providers.filter((p) => p.provider_type === "ldap");

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-br from-background to-subtle px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-3">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-accent shadow-lg">
            <Sparkles className="h-7 w-7 text-white" strokeWidth={2} />
          </div>
          <div className="text-center">
            <h1 className="text-2xl font-semibold text-foreground">管理控制台</h1>
            <p className="mt-1 text-sm text-secondary">企业智能体操作系统 · 管理员入口</p>
          </div>
        </div>

        <BackendUrlBanner className="mb-4" />

        <form
          onSubmit={handleSubmit}
          className="rounded-xl border border-border bg-elevated p-6 shadow-sm"
        >
          <label className="mb-1.5 block text-sm font-medium text-foreground">管理员邮箱</label>
          <div className="relative">
            <Mail className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-tertiary" />
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="admin@acme.com"
              required
              autoFocus
              className="w-full rounded-lg border border-border bg-background py-2.5 pl-9 pr-3 text-sm text-foreground placeholder:text-tertiary focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
            />
          </div>

          {error && <p className="mt-2 text-xs text-danger">{error}</p>}

          <button
            type="submit"
            disabled={loading || !email.trim()}
            className="mt-4 flex w-full items-center justify-center gap-2 rounded-lg bg-accent py-2.5 text-sm font-medium text-white transition-colors hover:bg-accent-strong disabled:opacity-50"
          >
            {loading && <Loader2 className="h-4 w-4 animate-spin" />}
            {loading ? "登录中..." : "进入控制台"}
          </button>

          <p className="mt-3 text-center text-xs text-tertiary">仅限管理员账号登录</p>
        </form>

        {providers.length > 0 && (
          <div className="mt-4 space-y-3">
            <div className="flex items-center gap-3">
              <div className="h-px flex-1 bg-border" />
              <span className="text-xs text-tertiary">或通过 SSO 登录</span>
              <div className="h-px flex-1 bg-border" />
            </div>

            {oidcProviders.map((p) => (
              <button
                key={p.provider_key}
                onClick={() => handleOidcLogin(p.provider_key)}
                className="flex w-full items-center justify-center gap-2 rounded-lg border border-border bg-elevated py-2.5 text-sm font-medium text-foreground transition-colors hover:bg-subtle"
              >
                <KeyRound className="h-4 w-4" />
                {p.name}
              </button>
            ))}

            {ldapProviders.length > 0 && (
              <>
                <button
                  onClick={() => setShowLdapForm((v) => !v)}
                  className="flex w-full items-center justify-center gap-2 rounded-lg border border-border bg-elevated py-2.5 text-sm font-medium text-foreground transition-colors hover:bg-subtle"
                >
                  <LogIn className="h-4 w-4" />
                  LDAP 登录
                </button>
                {showLdapForm && (
                  <form
                    onSubmit={handleLdapLogin}
                    className="space-y-2 rounded-xl border border-border bg-elevated p-4 shadow-sm"
                  >
                    <select
                      className="w-full rounded-md border border-border bg-background px-2 py-2 text-sm"
                      value={ldapCreds.provider_key}
                      onChange={(e) => setLdapCreds({ ...ldapCreds, provider_key: e.target.value })}
                      required
                    >
                      <option value="">选择 LDAP 服务...</option>
                      {ldapProviders.map((p) => (
                        <option key={p.provider_key} value={p.provider_key}>
                          {p.name}
                        </option>
                      ))}
                    </select>
                    <input
                      type="text"
                      placeholder="用户名"
                      value={ldapCreds.username}
                      onChange={(e) => setLdapCreds({ ...ldapCreds, username: e.target.value })}
                      required
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                    />
                    <input
                      type="password"
                      placeholder="密码"
                      value={ldapCreds.password}
                      onChange={(e) => setLdapCreds({ ...ldapCreds, password: e.target.value })}
                      required
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                    />
                    <button
                      type="submit"
                      disabled={ldapLoading}
                      className="flex w-full items-center justify-center gap-2 rounded-lg bg-accent py-2 text-sm font-medium text-white hover:bg-accent-strong disabled:opacity-50"
                    >
                      {ldapLoading && <Loader2 className="h-4 w-4 animate-spin" />}
                      登录
                    </button>
                  </form>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
