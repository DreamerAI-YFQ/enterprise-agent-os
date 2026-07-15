import { useState, type FormEvent } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { Button, Input, Spinner, toast, BackendUrlBanner } from "@eaos/shared";
import { useAuth } from "@eaos/shared";
import { Sparkles, ArrowRight, AlertCircle } from "lucide-react";

interface LocationState {
  from?: { pathname: string };
}

export default function LoginPage() {
  const { login, isLoading } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);

  const from = (location.state as LocationState)?.from?.pathname;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const trimmed = email.trim();
    if (!trimmed) {
      setError("请输入企业邮箱");
      return;
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmed)) {
      setError("邮箱格式不正确");
      return;
    }
    try {
      await login(trimmed);
      toast.show({ title: "登录成功", variant: "success" });
      navigate(from ?? "/app", { replace: true });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "登录失败，请重试";
      setError(msg);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        {/* Brand */}
        <div className="mb-8 flex flex-col items-center gap-3">
          <div className="flex h-14 w-14 items-center justify-center rounded-xl bg-accent shadow-md">
            <Sparkles className="h-7 w-7 text-white" strokeWidth={2} />
          </div>
          <div className="text-center">
            <h1 className="text-2xl font-semibold tracking-tight text-foreground">
              欢迎使用 EAOS
            </h1>
            <p className="mt-1 text-sm text-secondary">
              企业智能体操作系统 · 员工工作台
            </p>
          </div>
        </div>

        {/* Backend URL hint for desktop mode */}
        <BackendUrlBanner className="mb-4" />

        {/* Card */}
        <form
          onSubmit={handleSubmit}
          className="rounded-lg border border-border bg-elevated p-8 shadow-sm"
        >
          <label className="mb-2 block text-sm font-medium text-foreground">
            企业邮箱
          </label>
          <Input
            type="email"
            placeholder="you@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            autoFocus
            disabled={isLoading}
            className="h-12"
          />

          {error && (
            <div className="mt-3 flex items-center gap-2 rounded-md bg-danger-subtle px-3 py-2 text-sm text-danger">
              <AlertCircle className="h-4 w-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <Button
            type="submit"
            size="lg"
            disabled={isLoading}
            className="mt-5 w-full"
          >
            {isLoading ? (
              <>
                <Spinner size="sm" className="text-white" />
                登录中…
              </>
            ) : (
              <>
                登录
                <ArrowRight className="h-4 w-4" />
              </>
            )}
          </Button>

          <p className="mt-4 text-center text-xs text-tertiary">
            使用企业邮箱免密登录 · 首次登录自动注册
          </p>
        </form>

        <p className="mt-6 text-center text-xs text-tertiary">
          © 2026 Enterprise Agent OS
        </p>
      </div>
    </div>
  );
}
