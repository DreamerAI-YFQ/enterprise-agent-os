import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import { Spinner, Badge, cn } from "@eaos/shared";
import { Database, Table2, Download, Key, Link2, Search } from "lucide-react";

interface Datasource {
  id: string;
  name: string;
  source_type: string;
  access_mode: string;
  status: string;
}

interface TableInfo {
  connector: string;
  name: string;
  display_name: string;
  description: string;
  access_mode: string;
}

interface ColumnInfo {
  name: string;
  type: string;
  nullable: boolean;
  comment: string | null;
}

interface Relation {
  from_table: string;
  from_col: string;
  to_table: string;
  to_col: string;
}

interface TableDetail {
  connector: string;
  table_name: string;
  columns: ColumnInfo[];
  relations: Relation[];
  sample_rows: Record<string, unknown>[];
}

export default function BiDataPage() {
  const [connector, setConnector] = useState("");
  const [selectedTable, setSelectedTable] = useState("");
  const [tableSearch, setTableSearch] = useState("");

  const datasourcesQuery = useQuery({
    queryKey: ["admin", "bi", "datasources"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/bi/datasources", {});
      if (error || !data) return [] as Datasource[];
      return data as unknown as Datasource[];
    },
  });

  const datasources = datasourcesQuery.data ?? [];

  const tablesQuery = useQuery({
    queryKey: ["admin", "bi", "tables", connector],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/bi/tables", {
        params: { query: connector ? { connector } : {} },
      });
      if (error || !data) return [] as TableInfo[];
      return data as unknown as TableInfo[];
    },
    enabled: datasources.length > 0,
  });

  const tables = tablesQuery.data ?? [];

  const filteredTables = useMemo(() => {
    if (!tableSearch.trim()) return tables;
    const q = tableSearch.toLowerCase();
    return tables.filter(
      (t) =>
        t.name.toLowerCase().includes(q) ||
        t.display_name?.toLowerCase().includes(q)
    );
  }, [tables, tableSearch]);

  const detailQuery = useQuery({
    queryKey: ["admin", "bi", "table", selectedTable, connector],
    queryFn: async () => {
      const { data, error } = await apiClient.GET(
        "/admin/bi/tables/{table_name}",
        {
          params: {
            path: { table_name: selectedTable },
            query: connector ? { connector } : {},
          },
        }
      );
      if (error || !data) return null;
      return data as unknown as TableDetail;
    },
    enabled: selectedTable.length > 0,
  });

  const detail = detailQuery.data;

  const handleExportCsv = () => {
    if (!detail || detail.sample_rows.length === 0) return;
    const cols = detail.columns.map((c) => c.name);
    const header = cols.join(",");
    const rows = detail.sample_rows.map((r) =>
      cols
        .map((c) => {
          const v = r[c];
          if (v === null || v === undefined) return "";
          const s = String(v).replace(/"/g, '""');
          return `"${s}"`;
        })
        .join(",")
    );
    const csv = [header, ...rows].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${detail.table_name}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (datasourcesQuery.isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <h1 className="text-2xl font-semibold text-foreground">数据浏览</h1>
        <p className="mt-1 text-sm text-secondary">
          浏览数据源、表结构和样例数据
        </p>
      </div>

      <div className="border-b border-border-subtle px-8 py-3">
        <div className="flex items-center gap-2">
          <Database className="h-4 w-4 text-tertiary" />
          <select
            value={connector}
            onChange={(e) => {
              setConnector(e.target.value);
              setSelectedTable("");
            }}
            className="rounded-md border border-border bg-elevated px-3 py-1.5 text-sm text-foreground focus:border-accent focus:outline-none"
          >
            <option value="">全部数据源</option>
            {datasources.map((ds) => (
              <option key={ds.id} value={ds.name}>
                {ds.name} · {ds.source_type}
              </option>
            ))}
          </select>
          {datasources.length === 0 && (
            <span className="text-xs text-tertiary">暂无数据源</span>
          )}
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Table List */}
        <div className="flex w-64 shrink-0 flex-col border-r border-border-subtle">
          <div className="border-b border-border-subtle p-3">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-tertiary" />
              <input
                type="text"
                placeholder="搜索表名..."
                value={tableSearch}
                onChange={(e) => setTableSearch(e.target.value)}
                className="w-full rounded-md border border-border bg-elevated py-1.5 pl-8 pr-2 text-xs text-foreground placeholder:text-tertiary focus:border-accent focus:outline-none"
              />
            </div>
          </div>
          <div className="flex-1 overflow-y-auto">
            {tablesQuery.isLoading ? (
              <div className="flex h-20 items-center justify-center">
                <Spinner />
              </div>
            ) : filteredTables.length === 0 ? (
              <div className="flex h-20 items-center justify-center">
                <span className="text-xs text-tertiary">暂无表</span>
              </div>
            ) : (
              filteredTables.map((t) => (
                <button
                  key={`${t.connector}-${t.name}`}
                  onClick={() => setSelectedTable(t.name)}
                  className={cn(
                    "flex w-full items-center gap-2 border-l-2 px-3 py-2 text-left text-sm transition-colors duration-fast",
                    selectedTable === t.name
                      ? "border-accent bg-accent-subtle/30 text-foreground"
                      : "border-transparent text-secondary hover:bg-subtle/50 hover:text-foreground"
                  )}
                >
                  <Table2 className="h-3.5 w-3.5 shrink-0 text-tertiary" />
                  <span className="truncate">{t.display_name || t.name}</span>
                </button>
              ))
            )}
          </div>
        </div>

        {/* Table Detail */}
        <div className="flex-1 overflow-y-auto">
          {!selectedTable ? (
            <div className="flex h-full items-center justify-center p-8">
              <div className="flex flex-col items-center gap-3 text-center">
                <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                  <Table2 className="h-8 w-8" strokeWidth={1.5} />
                </div>
                <h3 className="text-lg font-medium text-foreground">
                  选择一个表
                </h3>
                <p className="max-w-sm text-sm text-secondary">
                  从左侧列表选择表，查看字段结构、关系和样例数据
                </p>
              </div>
            </div>
          ) : detailQuery.isLoading ? (
            <div className="flex h-full items-center justify-center">
              <Spinner />
            </div>
          ) : !detail ? (
            <div className="flex h-full items-center justify-center p-8">
              <div className="flex flex-col items-center gap-3 text-center">
                <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                  <Table2 className="h-8 w-8" strokeWidth={1.5} />
                </div>
                <h3 className="text-lg font-medium text-foreground">
                  无法加载表详情
                </h3>
                <p className="max-w-sm text-sm text-secondary">
                  表可能不存在或数据源连接失败
                </p>
              </div>
            </div>
          ) : (
            <div className="p-6">
              {/* Header */}
              <div className="mb-6 flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-foreground">
                    {detail.table_name}
                  </h2>
                  <p className="mt-0.5 text-xs text-tertiary">
                    {detail.connector} · {detail.columns.length} 列 ·{" "}
                    {detail.sample_rows.length} 行样例
                  </p>
                </div>
                {detail.sample_rows.length > 0 && (
                  <button
                    onClick={handleExportCsv}
                    className="flex items-center gap-1.5 rounded-md border border-border bg-elevated px-3 py-1.5 text-xs font-medium text-secondary transition-colors hover:bg-subtle hover:text-foreground"
                  >
                    <Download className="h-3.5 w-3.5" />
                    导出 CSV
                  </button>
                )}
              </div>

              {/* Columns */}
              <div className="mb-6">
                <h3 className="mb-2 text-sm font-medium text-foreground">
                  字段结构
                </h3>
                <div className="overflow-hidden rounded-md border border-border">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border bg-subtle/50">
                        <th className="px-3 py-2 text-left text-xs font-medium text-secondary">
                          字段名
                        </th>
                        <th className="px-3 py-2 text-left text-xs font-medium text-secondary">
                          类型
                        </th>
                        <th className="px-3 py-2 text-left text-xs font-medium text-secondary">
                          可空
                        </th>
                        <th className="px-3 py-2 text-left text-xs font-medium text-secondary">
                          备注
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.columns.map((col) => (
                        <tr
                          key={col.name}
                          className="border-b border-border-subtle last:border-0"
                        >
                          <td className="px-3 py-2 font-mono text-xs text-foreground">
                            <span className="flex items-center gap-1.5">
                              {!col.nullable && (
                                <Key className="h-3 w-3 text-warning" />
                              )}
                              {col.name}
                            </span>
                          </td>
                          <td className="px-3 py-2">
                            <Badge variant="secondary" className="text-xs">
                              {col.type}
                            </Badge>
                          </td>
                          <td className="px-3 py-2 text-xs text-tertiary">
                            {col.nullable ? "是" : "否"}
                          </td>
                          <td className="px-3 py-2 text-xs text-secondary">
                            {col.comment || "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Relations */}
              {detail.relations.length > 0 && (
                <div className="mb-6">
                  <h3 className="mb-2 text-sm font-medium text-foreground">
                    外键关系
                  </h3>
                  <div className="space-y-1.5">
                    {detail.relations.map((rel, idx) => (
                      <div
                        key={idx}
                        className="flex items-center gap-2 rounded-md border border-border bg-subtle/30 px-3 py-2 text-xs"
                      >
                        <Link2 className="h-3.5 w-3.5 text-accent" />
                        <span className="font-mono text-foreground">
                          {rel.from_table}.{rel.from_col}
                        </span>
                        <span className="text-tertiary">→</span>
                        <span className="font-mono text-foreground">
                          {rel.to_table}.{rel.to_col}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Sample Rows */}
              {detail.sample_rows.length > 0 ? (
                <div>
                  <h3 className="mb-2 text-sm font-medium text-foreground">
                    样例数据
                  </h3>
                  <div className="overflow-x-auto rounded-md border border-border">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-border bg-subtle/50">
                          {detail.columns.map((c) => (
                            <th
                              key={c.name}
                              className="px-3 py-2 text-left text-xs font-medium text-secondary"
                            >
                              {c.name}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {detail.sample_rows.map((row, idx) => (
                          <tr
                            key={idx}
                            className={cn(
                              "border-b border-border-subtle last:border-0",
                              idx % 2 === 1 && "bg-subtle/20"
                            )}
                          >
                            {detail.columns.map((c) => (
                              <td
                                key={c.name}
                                className="px-3 py-2 font-mono text-xs text-foreground"
                              >
                                {String(row[c.name] ?? "")}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ) : (
                <div className="rounded-md border border-border bg-subtle/30 px-4 py-8 text-center">
                  <p className="text-xs text-tertiary">暂无样例数据</p>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
