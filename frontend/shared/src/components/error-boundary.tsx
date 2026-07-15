import { Component, type ErrorInfo, type ReactNode } from "react";
import { AlertTriangle, RefreshCw, Home } from "lucide-react";
import { Button } from "./ui/button";

interface ErrorBoundaryProps {
  children: ReactNode;
  /** Render boundary inline (for route-level) instead of full-screen. */
  inline?: boolean;
  /** Custom reset hook; if provided, boundary will call it on retry. */
  onReset?: () => void;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * Global error boundary — catches React rendering exceptions and shows
 * a friendly recovery UI instead of a blank white screen.
 *
 * Apple-style design: calm error state, clear single-action recovery,
 * no scary red flashing. Inline variant used for route-level boundaries.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Best-effort log; never crash here.
    try {
      // eslint-disable-next-line no-console
      console.error("[ErrorBoundary]", error, info.componentStack);
    } catch {
      // ignore
    }
  }

  private handleReset = () => {
    this.setState({ error: null });
    this.props.onReset?.();
  };

  private handleReload = () => {
    window.location.reload();
  };

  private handleHome = () => {
    this.setState({ error: null });
    window.location.assign("/");
  };

  render() {
    const { error } = this.state;
    const { inline = false } = this.props;

    if (!error) return this.props.children;

    if (inline) {
      return (
        <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-warning/10 text-warning">
            <AlertTriangle className="h-6 w-6" strokeWidth={1.75} />
          </div>
          <h3 className="text-lg font-semibold text-foreground">页面出错了</h3>
          <p className="max-w-md text-sm text-secondary">
            渲染过程中出现异常。可以重试或返回首页。
          </p>
          <div className="mt-1 flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={this.handleReset}>
              <RefreshCw className="h-3.5 w-3.5" />
              重试
            </Button>
            <Button variant="ghost" size="sm" onClick={this.handleHome}>
              <Home className="h-3.5 w-3.5" />
              返回首页
            </Button>
          </div>
        </div>
      );
    }

    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-background px-6 text-center">
        <div className="flex h-16 w-16 items-center justify-center rounded-full bg-warning/10 text-warning">
          <AlertTriangle className="h-8 w-8" strokeWidth={1.5} />
        </div>
        <h1 className="text-2xl font-semibold text-foreground">应用出现异常</h1>
        <p className="max-w-md text-sm text-secondary">
          抱歉，遇到了未预期的错误。请尝试刷新页面；如问题持续，请联系管理员。
        </p>
        {error.message && (
          <pre className="mt-2 max-w-lg overflow-x-auto rounded-md bg-subtle px-3 py-2 text-left text-xs text-tertiary">
            {error.message}
          </pre>
        )}
        <div className="mt-2 flex items-center gap-3">
          <Button onClick={this.handleReload}>
            <RefreshCw className="h-4 w-4" />
            刷新页面
          </Button>
          <Button variant="outline" onClick={this.handleHome}>
            <Home className="h-4 w-4" />
            返回首页
          </Button>
        </div>
      </div>
    );
  }
}
