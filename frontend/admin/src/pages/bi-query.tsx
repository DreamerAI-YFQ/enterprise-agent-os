import { useState, useEffect, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import { Spinner, cn } from "@eaos/shared";
import { BarChart3, Database, Table2, Sparkles, Star, History, Trash2, Pencil, RotateCcw } from "lucide-react";

interface Datasource {
  id: string;
  name: string;
  source_type: string;
}

interface BiResult {
  rows: Record<string, unknown>[];
  sql: string;
  explanation: string | null;
  truncated: boolean;
  error: string | null;
}

interface QueryHistoryItem {
  query: string;
  sql: string;
  timestamp: string;
  favorite: boolean;
}

const STORAGE_KEY = "eaos:bi-query-history";

function loadHistory(): QueryHistoryItem[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as QueryHistoryItem[];
  } catch {
    return [];
  }
}

function saveHistory(items: QueryHistoryItem[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items.slice(0, 30)));
  } catch {
    // ignore
  }
}

export default function BiQueryPage() {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [datasourceId, setDatasourceId] = useState("");
  const [editedSql, setEditedSql] = useState<string | null>(null);
  const [history, setHistory] = useState<QueryHistoryItem[]>(loadHistory);
  const [showHistory, setShowHistory] = useState(false);

  const datasourcesQuery = useQuery({
    queryKey: ["admin", "bi", "datasources"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/bi/datasources", {});
      if (error || !data) return [] as Datasource[];
      return data as unknown as Datasource[];
    },
  });

  const datasources = useMemo(
    () => datasourcesQuery.data ?? [],
    [datasourcesQuery.data],
  );
  useEffect(() => {
    if (datasources.length > 0 && !datasourceId) {
      setDatasourceId(datasources[0].id);
    }
  }, [datasources, datasourceId]);

  const biQuery = useQuery({
    queryKey: ["bi-query", submitted, datasourceId],
    queryFn: async () => {
      const { data, error } = await apiClient.POST("/bi/query", {
        body: { query: submitted, datasource_id: datasourceId },
      });
      if (error || !data) return null;
      return data as unknown as BiResult;
    },
    enabled: submitted.length > 0 && datasourceId.length > 0 && editedSql === null,
  });

  const sqlRerunQuery = useQuery({
    queryKey: ["admin", "bi", "sql-rerun", editedSql],
    queryFn: async () => {
      const { data, error } = await apiClient.POST("/admin/bi/sql", {
        body: { sql: editedSql!, params: [] },
      });
      if (error || !data) return null;
      const result = data as unknown as { rows: Record<string, unknown>[]; row_count: number };
      return {
        rows: result.rows,
        sql: editedSql!,
        explanation: null,
        truncated: false,
        error: null,
      } as BiResult;
    },
    enabled: editedSql !== null && editedSql.length > 0,
  });

  const result = editedSql !== null ? sqlRerunQuery.data : biQuery.data;
  const isLoading = editedSql !== null ? sqlRerunQuery.isLoading : biQuery.isLoading;
  const isSuccess = editedSql !== null ? sqlRerunQuery.isSuccess : biQuery.isSuccess;
  const isFetching = editedSql !== null ? sqlRerunQuery.isFetching : biQuery.isFetching;

  const rows = result?.rows ?? [];
  const columns = rows.length > 0 ? Object.keys(rows[0]) : [];

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (query.trim() && datasourceId) {
      setEditedSql(null);
      setSubmitted(query.trim());
    }
  };

  const handleRerun = () => {
    if (editedSql) {
      sqlRerunQuery.refetch();
    }
  };

  useEffect(() => {
    if (isSuccess && result && submitted) {
      const existing = history.find(
        (h) => h.query === submitted && h.sql === result.sql
      );
      if (!existing) {
        const item: QueryHistoryItem = {
          query: submitted,
          sql: result.sql,
          timestamp: new Date().toISOString(),
          favorite: false,
        };
        const newHistory = [item, ...history].slice(0, 30);
        setHistory(newHistory);
        saveHistory(newHistory);
      }
    }
  }, [isSuccess, result, submitted, history]);

  const toggleFavorite = (idx: number) => {
    const newHistory = history.map((h, i) =>
      i === idx ? { ...h, favorite: !h.favorite } : h
    );
    setHistory(newHistory);
    saveHistory(newHistory);
  };

  const removeHistory = (idx: number) => {
    const newHistory = history.filter((_, i) => i !== idx);
    setHistory(newHistory);
    saveHistory(newHistory);
  };

  const replayQuery = (item: QueryHistoryItem) => {
    setQuery(item.query);
    setEditedSql(null);
    setSubmitted(item.query);
    setShowHistory(false);
  };

  const favorites = history.filter((h) => h.favorite);
  const recentHistory = history.filter((h) => !h.favorite);

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">自然语言查询</h1>
            <p className="mt-1 text-sm text-secondary">
              用自然语言提问，自动生成 SQL 并执行
            </p>
          </div>
          <button
            onClick={() => setShowHistory(!showHistory)}
            className={cn(
              "flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-xs font-medium transition-colors",
              showHistory
                ? "border-accent bg-accent-subtle text-accent"
                : "border-border text-secondary hover:bg-subtle"
            )}
          >
            <History className="h-3.5 w-3.5" />
            历史 ({history.length})
          </button>
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Main */}
        <div className="flex flex-1 flex-col overflow-hidden">
          {/* Query Input */}
          <div className="border-b border-border-subtle px-8 py-4">
            <form onSubmit={handleSubmit} className="space-y-2">
              <div className="flex items-center gap-2">
                <div className="flex items-center gap-1.5 rounded-md border border-border bg-subtle px-2.5 py-1.5">
                  <Database className="h-3.5 w-3.5 text-tertiary" />
                  <select
                    value={datasourceId}
                    onChange={(e) => setDatasourceId(e.target.value)}
                    className="bg-transparent text-xs text-foreground focus:outline-none"
                  >
                    {datasources.length === 0 && (
                      <option value="">无可用数据源</option>
                    )}
                    {datasources.map((ds) => (
                      <option key={ds.id} value={ds.id}>
                        {ds.name} · {ds.source_type}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <div className="relative flex-1">
                  <Sparkles className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-tertiary" />
                  <input
                    type="text"
                    placeholder="例如：上个月销售额最高的5个产品"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    className="w-full rounded-md border border-border bg-elevated py-2 pl-9 pr-3 text-sm text-foreground placeholder:text-tertiary focus:border-accent focus:outline-none"
                  />
                </div>
                <button
                  type="submit"
                  disabled={!query.trim() || !datasourceId || isFetching}
                  className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-strong disabled:opacity-50"
                >
                  {isFetching ? "查询中..." : "查询"}
                </button>
              </div>
            </form>
          </div>

          {/* Results */}
          <div className="flex-1 overflow-y-auto px-8 py-6">
            {!submitted ? (
              <div className="flex h-full items-center justify-center">
                <div className="flex flex-col items-center gap-3 text-center">
                  <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                    <BarChart3 className="h-8 w-8" strokeWidth={1.5} />
                  </div>
                  <h3 className="text-lg font-medium text-foreground">
                    开始查询
                  </h3>
                  <p className="max-w-md text-sm text-secondary">
                    输入你想了解的数据问题，系统自动翻译成 SQL 并返回结果
                  </p>
                </div>
              </div>
            ) : isLoading ? (
              <div className="flex h-40 items-center justify-center">
                <Spinner />
              </div>
            ) : result?.error ? (
              <div className="rounded-md border border-danger/30 bg-danger/5 p-4">
                <p className="text-sm font-medium text-danger">查询失败</p>
                <p className="mt-1 text-xs text-secondary">{result.error}</p>
              </div>
            ) : result ? (
              <div className="space-y-4">
                {result.explanation && (
                  <div className="rounded-md border border-accent/30 bg-accent-subtle/30 p-3">
                    <p className="text-xs text-secondary">{result.explanation}</p>
                  </div>
                )}
                {/* SQL with edit */}
                <div className="rounded-md border border-border bg-subtle/50">
                  <div className="flex items-center justify-between border-b border-border-subtle px-3 py-1.5">
                    <span className="text-xs font-medium text-secondary">生成的 SQL</span>
                    <div className="flex items-center gap-1">
                      {editedSql !== null && editedSql !== result.sql ? (
                        <>
                          <button
                            onClick={handleRerun}
                            className="flex items-center gap-1 rounded px-2 py-1 text-xs text-accent hover:bg-accent-subtle"
                          >
                            <RotateCcw className="h-3 w-3" />
                            重跑
                          </button>
                          <button
                            onClick={() => setEditedSql(null)}
                            className="rounded px-2 py-1 text-xs text-tertiary hover:bg-subtle"
                          >
                            恢复
                          </button>
                        </>
                      ) : (
                        <button
                          onClick={() => setEditedSql(result.sql)}
                          className="flex items-center gap-1 rounded px-2 py-1 text-xs text-secondary hover:bg-subtle hover:text-foreground"
                        >
                          <Pencil className="h-3 w-3" />
                          编辑
                        </button>
                      )}
                    </div>
                  </div>
                  {editedSql !== null ? (
                    <textarea
                      value={editedSql}
                      onChange={(e) => setEditedSql(e.target.value)}
                      className="w-full resize-none bg-transparent px-3 py-2 font-mono text-xs text-foreground focus:outline-none"
                      rows={Math.min(8, editedSql.split("\n").length + 1)}
                      spellCheck={false}
                    />
                  ) : (
                    <pre className="overflow-x-auto px-3 py-2 font-mono text-xs text-foreground">
                      {result.sql}
                    </pre>
                  )}
                </div>
                {result.truncated && (
                  <p className="text-xs text-warning">结果已截断，仅显示前部分行</p>
                )}
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
                    <Table2 className="mx-auto h-8 w-8 text-tertiary" strokeWidth={1.5} />
                    <p className="mt-2 text-xs text-tertiary">查询无结果</p>
                  </div>
                )}
                <p className="text-xs text-tertiary">共 {rows.length} 行</p>
              </div>
            ) : null}
          </div>
        </div>

        {/* History Panel */}
        {showHistory && (
          <div className="flex w-72 shrink-0 flex-col border-l border-border-subtle">
            <div className="border-b border-border-subtle px-4 py-3">
              <span className="text-xs font-medium text-foreground">查询历史</span>
            </div>
            <div className="flex-1 overflow-y-auto">
              {favorites.length > 0 && (
                <>
                  <p className="px-4 py-1.5 text-xs font-medium text-tertiary">
                    收藏
                  </p>
                  {favorites.map((item, idx) => {
                    const realIdx = history.indexOf(item);
                    return (
                      <div
                        key={`fav-${idx}`}
                        className="group border-b border-border-subtle px-4 py-2.5"
                      >
                        <div className="flex items-center justify-between">
                          <button
                            onClick={() => replayQuery(item)}
                            className="flex-1 text-left"
                          >
                            <p className="truncate text-xs text-foreground">
                              {item.query}
                            </p>
                            <p className="mt-0.5 text-xs text-tertiary">
                              {new Date(item.timestamp).toLocaleDateString("zh-CN")}
                            </p>
                          </button>
                          <button
                            onClick={() => toggleFavorite(realIdx)}
                            className="text-warning hover:opacity-70"
                          >
                            <Star className="h-3.5 w-3.5 fill-warning" />
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </>
              )}
              <p className="px-4 py-1.5 text-xs font-medium text-tertiary">
                最近
              </p>
              {recentHistory.length === 0 ? (
                <p className="px-4 py-8 text-center text-xs text-tertiary">
                  暂无历史
                </p>
              ) : (
                recentHistory.map((item, idx) => {
                  const realIdx = history.indexOf(item);
                  return (
                    <div
                      key={`rec-${idx}`}
                      className="group border-b border-border-subtle px-4 py-2.5"
                    >
                      <div className="flex items-center justify-between">
                        <button
                          onClick={() => replayQuery(item)}
                          className="flex-1 text-left"
                        >
                          <p className="truncate text-xs text-foreground">
                            {item.query}
                          </p>
                          <p className="mt-0.5 text-xs text-tertiary">
                            {new Date(item.timestamp).toLocaleDateString("zh-CN")}
                          </p>
                        </button>
                        <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                          <button
                            onClick={() => toggleFavorite(realIdx)}
                            className="text-tertiary hover:text-warning"
                          >
                            <Star className="h-3.5 w-3.5" />
                          </button>
                          <button
                            onClick={() => removeHistory(realIdx)}
                            className="text-tertiary hover:text-danger"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
