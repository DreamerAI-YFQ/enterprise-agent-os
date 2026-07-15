import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import { Spinner, cn } from "@eaos/shared";
import { Terminal, Play, AlertTriangle, CheckCircle2, Trash2, Clock } from "lucide-react";

interface SqlResult {
  rows: Record<string, unknown>[];
  row_count: number;
}

interface HistoryItem {
  sql: string;
  timestamp: string;
  success: boolean;
  rowCount: number;
}

const STORAGE_KEY = "eaos:sql-history";

function loadHistory(): HistoryItem[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as HistoryItem[];
  } catch {
    return [];
  }
}

function saveHistory(items: HistoryItem[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items.slice(0, 20)));
  } catch {
    // ignore
  }
}

export default function BiSqlPage() {
  const [sql, setSql] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>(loadHistory);

  const sqlQuery = useQuery({
    queryKey: ["admin", "bi", "sql", submitted],
    queryFn: async () => {
      const { data, error: apiError } = await apiClient.POST("/admin/bi/sql", {
        body: { sql: submitted, params: [] },
      });
      if (apiError) {
        const detail = (apiError as { detail?: string })?.detail;
        throw new Error(detail ?? "SQL 执行失败");
      }
      if (!data) return null;
      return data as unknown as SqlResult;
    },
    enabled: submitted.length > 0,
    retry: false,
  });

  const handleExecute = (e: React.FormEvent) => {
    e.preventDefault();
    if (!sql.trim()) return;
    setError(null);
    setSubmitted(sql.trim());
  };

  if (sqlQuery.isError && !error) {
    const msg = sqlQuery.error instanceof Error ? sqlQuery.error.message : "执行失败";
    setError(msg);
    const item: HistoryItem = {
      sql: submitted,
      timestamp: new Date().toISOString(),
      success: false,
      rowCount: 0,
    };
    const newHistory = [item, ...history].slice(0, 20);
    setHistory(newHistory);
    saveHistory(newHistory);
  }

  if (sqlQuery.isSuccess && submitted && !history.some((h) => h.sql === submitted && h.success)) {
    const result = sqlQuery.data;
    const item: HistoryItem = {
      sql: submitted,
      timestamp: new Date().toISOString(),
      success: true,
      rowCount: result?.row_count ?? 0,
    };
    const newHistory = [item, ...history.filter((h) => h.sql !== submitted)].slice(0, 20);
    setHistory(newHistory);
    saveHistory(newHistory);
  }

  const result = sqlQuery.data;
  const rows = result?.rows ?? [];
  const columns = rows.length > 0 ? Object.keys(rows[0]) : [];

  const clearHistory = () => {
    setHistory([]);
    saveHistory([]);
  };

  const replayHistory = (item: HistoryItem) => {
    setSql(item.sql);
    setError(null);
    setSubmitted(item.sql);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <h1 className="text-2xl font-semibold text-foreground">SQL 控制台</h1>
        <p className="mt-1 text-sm text-secondary">
          在沙箱中执行只读 SQL 查询
        </p>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Main */}
        <div className="flex flex-1 flex-col overflow-hidden">
          {/* Sandbox Notice */}
          <div className="flex items-center gap-2 border-b border-border-subtle bg-warning/5 px-8 py-2">
            <AlertTriangle className="h-3.5 w-3.5 text-warning" />
            <span className="text-xs text-secondary">
              沙箱模式：仅允许 SELECT 查询，禁止 DDL/DML 写操作
            </span>
          </div>

          {/* Editor */}
          <form onSubmit={handleExecute} className="border-b border-border-subtle bg-subtle/30">
            <div className="flex items-center justify-between border-b border-border-subtle px-4 py-2">
              <div className="flex items-center gap-2 text-xs text-tertiary">
                <Terminal className="h-3.5 w-3.5" />
                <span>SQL 编辑器</span>
              </div>
              <button
                type="submit"
                disabled={!sql.trim() || sqlQuery.isFetching}
                className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-accent-strong disabled:opacity-50"
              >
                <Play className="h-3.5 w-3.5" />
                {sqlQuery.isFetching ? "执行中..." : "执行 (Ctrl+Enter)"}
              </button>
            </div>
            <textarea
              value={sql}
              onChange={(e) => setSql(e.target.value)}
              onKeyDown={(e) => {
                if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
                  e.preventDefault();
                  handleExecute(e as unknown as React.FormEvent);
                }
              }}
              placeholder="SELECT * FROM customers LIMIT 10;"
              className="h-32 w-full resize-none bg-transparent px-4 py-3 font-mono text-sm text-foreground placeholder:text-tertiary focus:outline-none"
              spellCheck={false}
            />
          </form>

          {/* Results */}
          <div className="flex-1 overflow-y-auto px-8 py-4">
            {!submitted ? (
              <div className="flex h-full items-center justify-center">
                <div className="flex flex-col items-center gap-3 text-center">
                  <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                    <Terminal className="h-8 w-8" strokeWidth={1.5} />
                  </div>
                  <h3 className="text-lg font-medium text-foreground">
                    开始查询
                  </h3>
                  <p className="max-w-sm text-sm text-secondary">
                    输入 SQL 并按 Ctrl+Enter 执行
                  </p>
                </div>
              </div>
            ) : sqlQuery.isFetching ? (
              <div className="flex h-40 items-center justify-center">
                <Spinner />
              </div>
            ) : error ? (
              <div className="rounded-md border border-danger/30 bg-danger/5 p-4">
                <div className="flex items-center gap-2">
                  <AlertTriangle className="h-4 w-4 text-danger" />
                  <p className="text-sm font-medium text-danger">执行失败</p>
                </div>
                <pre className="mt-2 overflow-x-auto text-xs text-danger/80">
                  {error}
                </pre>
              </div>
            ) : result ? (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <CheckCircle2 className="h-4 w-4 text-success" />
                    <span className="text-sm font-medium text-foreground">
                      查询成功
                    </span>
                    <span className="text-xs text-tertiary">
                      {result.row_count} 行
                    </span>
                  </div>
                </div>
                {rows.length > 0 ? (
                  <div className="overflow-x-auto rounded-md border border-border">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-border bg-subtle/50">
                          {columns.map((col) => (
                            <th
                              key={col}
                              className="px-3 py-2 text-left text-xs font-medium text-secondary"
                            >
                              {col}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((row, idx) => (
                          <tr
                            key={idx}
                            className={cn(
                              "border-b border-border-subtle last:border-0",
                              idx % 2 === 1 && "bg-subtle/20"
                            )}
                          >
                            {columns.map((col) => (
                              <td
                                key={col}
                                className="px-3 py-2 font-mono text-xs text-foreground"
                              >
                                {String(row[col] ?? "")}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="rounded-md border border-border bg-subtle/30 px-4 py-8 text-center">
                    <p className="text-xs text-tertiary">查询无结果</p>
                  </div>
                )}
              </div>
            ) : null}
          </div>
        </div>

        {/* History Sidebar */}
        <div className="flex w-64 shrink-0 flex-col border-l border-border-subtle">
          <div className="flex items-center justify-between border-b border-border-subtle px-4 py-3">
            <div className="flex items-center gap-2">
              <Clock className="h-3.5 w-3.5 text-tertiary" />
              <span className="text-xs font-medium text-foreground">历史</span>
            </div>
            {history.length > 0 && (
              <button
                onClick={clearHistory}
                className="text-tertiary hover:text-danger"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
          <div className="flex-1 overflow-y-auto">
            {history.length === 0 ? (
              <p className="px-4 py-8 text-center text-xs text-tertiary">
                暂无执行历史
              </p>
            ) : (
              history.map((item, idx) => (
                <button
                  key={idx}
                  onClick={() => replayHistory(item)}
                  className="block w-full border-b border-border-subtle px-4 py-2.5 text-left transition-colors hover:bg-subtle/50"
                >
                  <div className="flex items-center gap-1.5">
                    {item.success ? (
                      <CheckCircle2 className="h-3 w-3 text-success shrink-0" />
                    ) : (
                      <AlertTriangle className="h-3 w-3 text-danger shrink-0" />
                    )}
                    <span className="text-xs text-tertiary">
                      {new Date(item.timestamp).toLocaleString("zh-CN", {
                        hour: "2-digit",
                        minute: "2-digit",
                        month: "2-digit",
                        day: "2-digit",
                      })}
                    </span>
                    {item.success && (
                      <span className="text-xs text-tertiary">
                        · {item.rowCount} 行
                      </span>
                    )}
                  </div>
                  <p className="mt-1 truncate font-mono text-xs text-secondary">
                    {item.sql}
                  </p>
                </button>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
