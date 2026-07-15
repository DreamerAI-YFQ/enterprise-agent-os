import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import { Spinner, cn } from "@eaos/shared";
import { BarChart3, Database, Table2, Sparkles } from "lucide-react";

interface Agent {
  id: string;
  name: string;
  capability?: {
    allowed_datasources?: string[];
  };
}

interface BiResult {
  rows: Record<string, unknown>[];
  sql: string;
  explanation: string | null;
  truncated: boolean;
  error: string | null;
}

export default function BiPage() {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [datasourceId, setDatasourceId] = useState("");

  // Fetch agents to get allowed_datasources from the first agent.
  const agentsQuery = useQuery({
    queryKey: ["agents"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/agents", {});
      if (error || !data) return [] as Agent[];
      return data as unknown as Agent[];
    },
  });

  const datasources = useMemo(() => {
    const agents = agentsQuery.data ?? [];
    const ids = new Set<string>();
    for (const a of agents) {
      for (const d of a.capability?.allowed_datasources ?? []) {
        ids.add(d);
      }
    }
    return Array.from(ids);
  }, [agentsQuery.data]);

  // Auto-select first datasource when loaded.
  if (datasources.length > 0 && !datasourceId) {
    setDatasourceId(datasources[0]);
  }

  const biQuery = useQuery({
    queryKey: ["bi-query", submitted, datasourceId],
    queryFn: async () => {
      const { data, error } = await apiClient.POST("/bi/query", {
        body: {
          query: submitted,
          datasource_id: datasourceId,
        },
      });
      if (error || !data) return null;
      return data as unknown as BiResult;
    },
    enabled: submitted.length > 0 && datasourceId.length > 0,
  });

  const result = biQuery.data;
  const rows = result?.rows ?? [];
  const columns = rows.length > 0 ? Object.keys(rows[0]) : [];

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (query.trim() && datasourceId) {
      setSubmitted(query.trim());
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <h1 className="text-2xl font-semibold text-foreground">数据查询</h1>
        <p className="mt-1 text-sm text-secondary">
          用自然语言提问，Agent 自动生成 SQL 并执行
        </p>
      </div>

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
                {datasources.map((id, idx) => (
                  <option key={id} value={id}>
                    数据源 {idx + 1} · {id.slice(0, 8)}
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
              disabled={!query.trim() || !datasourceId || biQuery.isFetching}
              className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-strong disabled:opacity-50"
            >
              {biQuery.isFetching ? "查询中..." : "查询"}
            </button>
          </div>
        </form>
      </div>

      <div className="flex-1 overflow-y-auto px-8 pb-8">
        {!submitted ? (
          <div className="flex h-full items-center justify-center p-8">
            <div className="flex flex-col items-center gap-3 text-center">
              <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                <BarChart3 className="h-8 w-8" strokeWidth={1.5} />
              </div>
              <h3 className="text-2xl font-semibold text-foreground">
                开始查询
              </h3>
              <p className="max-w-md text-sm text-secondary">
                输入你想了解的数据问题，Agent 会自动翻译成 SQL 并返回结果
              </p>
            </div>
          </div>
        ) : biQuery.isLoading ? (
          <div className="flex h-40 items-center justify-center">
            <Spinner />
          </div>
        ) : result?.error ? (
          <div className="rounded-md border border-danger/30 bg-danger/5 p-4">
            <p className="text-sm font-medium text-danger">查询失败</p>
            <p className="mt-1 text-xs text-secondary">{result.error}</p>
          </div>
        ) : rows.length === 0 ? (
          <div className="flex h-full items-center justify-center p-8">
            <div className="flex flex-col items-center gap-3 text-center">
              <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                <Table2 className="h-8 w-8" strokeWidth={1.5} />
              </div>
              <h3 className="text-2xl font-semibold text-foreground">
                查询无结果
              </h3>
              <p className="max-w-sm text-sm text-secondary">
                尝试换个问法或检查数据源
              </p>
            </div>
          </div>
        ) : result ? (
          <div className="space-y-4">
            {result.explanation && (
              <div className="rounded-md border border-accent/30 bg-accent-subtle/30 p-3">
                <p className="text-xs text-secondary">{result.explanation}</p>
              </div>
            )}
            <details className="rounded-md border border-border bg-subtle/50">
              <summary className="cursor-pointer px-3 py-2 text-xs font-medium text-secondary">
                生成的 SQL
              </summary>
              <pre className="overflow-x-auto px-3 py-2 text-xs text-foreground">
                {result.sql}
              </pre>
            </details>
            {result.truncated && (
              <p className="text-xs text-warning">
                结果已截断，仅显示前部分行
              </p>
            )}
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
                        <td key={col} className="px-3 py-2 text-foreground">
                          {String(row[col] ?? "")}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-xs text-tertiary">共 {rows.length} 行</p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
