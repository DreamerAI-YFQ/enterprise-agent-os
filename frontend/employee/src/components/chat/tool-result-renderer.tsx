import { useState } from "react";
import {
  Table as TableIcon,
  List as ListIcon,
  Database as DatabaseIcon,
  AlertCircle,
  ChevronRight,
  ChevronDown,
} from "lucide-react";
import type { AgentEvent } from "@eaos/shared";

/**
 * Smart renderer for tool_call event metadata.
 *
 * Detects the tool type (mcp/rag/skill) and renders structured output:
 * - MCP with `rows` → HTML table
 * - MCP with `resources` → resource card list
 * - MCP with `columns` → schema definition table
 * - RAG → search result list with score bars
 * - Skill → execution result card
 * - Fallback → collapsible JSON
 */

interface ToolResultRendererProps {
  event: AgentEvent;
}

export function ToolResultRenderer({ event }: ToolResultRendererProps) {
  const meta = event.metadata;
  if (!meta) {
    return <FallbackJson value={event.content} />;
  }

  const type = meta.type as string | undefined;
  if (type === "mcp") {
    return <McpResultRenderer meta={meta} />;
  }
  if (type === "rag") {
    return <RagResultRenderer meta={meta} />;
  }
  if (type === "skill") {
    return <SkillResultRenderer meta={meta} />;
  }
  return <FallbackJson value={JSON.stringify(meta, null, 2)} />;
}

// -- MCP ---------------------------------------------------------------------

function McpResultRenderer({ meta }: { meta: Record<string, unknown> }) {
  const toolName = meta.tool_name as string | undefined;
  const result = meta.result as
    | { content?: Array<{ text?: string }>; is_error?: boolean }
    | undefined;

  if (!result) {
    return <FallbackJson value={JSON.stringify(meta, null, 2)} />;
  }

  if (result.is_error) {
    const text = result.content?.[0]?.text ?? "unknown error";
    return (
      <div className="flex items-start gap-2 rounded-md bg-danger-subtle px-3 py-2 text-xs text-danger">
        <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <div>
          <span className="font-medium">{toolName ?? "tool"} 错误：</span>
          <span className="ml-1">{text}</span>
        </div>
      </div>
    );
  }

  // Parse the JSON payload from content[0].text
  const rawText = result.content?.[0]?.text;
  if (!rawText) {
    return <FallbackJson value={JSON.stringify(meta, null, 2)} />;
  }

  let payload: unknown;
  try {
    payload = JSON.parse(rawText);
  } catch {
    // Not JSON — show as text
    return (
      <SimpleText label={toolName} text={rawText} />
    );
  }

  if (typeof payload !== "object" || payload === null) {
    return <SimpleText label={toolName} text={String(payload)} />;
  }

  const obj = payload as Record<string, unknown>;

  if (Array.isArray(obj.rows)) {
    return <DataTable rows={obj.rows as Array<Record<string, unknown>>} total={obj.total as number | undefined} label={toolName} />;
  }
  if (Array.isArray(obj.resources)) {
    return <ResourceList resources={obj.resources as Array<Record<string, unknown>>} label={toolName} />;
  }
  if (Array.isArray(obj.columns)) {
    return <SchemaTable columns={obj.columns as Array<Record<string, unknown>>} tableName={obj.table_name as string | undefined} label={toolName} />;
  }
  if (obj.error) {
    return (
      <div className="flex items-start gap-2 rounded-md bg-danger-subtle px-3 py-2 text-xs text-danger">
        <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>{String(obj.error)}</span>
      </div>
    );
  }

  return <FallbackJson value={JSON.stringify(payload, null, 2)} />;
}

// -- RAG ---------------------------------------------------------------------

function RagResultRenderer({ meta }: { meta: Record<string, unknown> }) {
  const query = meta.query as string | undefined;
  const results = meta.results as Array<{ content?: string; score?: number }> | undefined;

  if (!results || results.length === 0) {
    return (
      <div className="rounded-md border border-border-subtle bg-elevated px-3 py-2 text-xs text-secondary">
        <DatabaseIcon className="mr-1.5 inline h-3 w-3" />
        知识库检索：无匹配结果
        {query && <span className="ml-1 text-tertiary">（{query}）</span>}
      </div>
    );
  }

  return (
    <div className="rounded-md border border-border-subtle bg-elevated">
      <div className="flex items-center gap-1.5 border-b border-border-subtle px-3 py-1.5 text-xs font-medium text-secondary">
        <DatabaseIcon className="h-3 w-3" />
        知识库检索 · {results.length} 条结果
      </div>
      <div className="max-h-60 space-y-1.5 overflow-y-auto p-2">
        {results.map((r, i) => {
          const score = typeof r.score === "number" ? r.score : 0;
          const pct = Math.round(score * 100);
          return (
            <div key={i} className="rounded bg-subtle px-2 py-1.5">
              <div className="mb-1 flex items-center gap-2">
                <div className="flex-1 truncate text-xs text-foreground">
                  {r.content ?? "(空)"}
                </div>
                <div className="shrink-0 text-xs text-tertiary">{pct}%</div>
              </div>
              <div className="h-1 overflow-hidden rounded-full bg-border-subtle">
                <div
                  className="h-full rounded-full bg-accent transition-all"
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// -- Skill -------------------------------------------------------------------

function SkillResultRenderer({ meta }: { meta: Record<string, unknown> }) {
  const skillName = meta.skill_name as string | undefined;
  const success = meta.success as boolean | undefined;
  const output = meta.output as string | undefined;
  const error = meta.error as string | null | undefined;

  return (
    <div className="rounded-md border border-border-subtle bg-elevated">
      <div className="flex items-center gap-1.5 border-b border-border-subtle px-3 py-1.5 text-xs font-medium text-secondary">
        <ListIcon className="h-3 w-3" />
        技能 · {skillName ?? "unknown"}
        {success !== undefined && (
          <span className={success ? "text-success" : "text-danger"}>
            {success ? " 成功" : " 失败"}
          </span>
        )}
      </div>
      {error && (
        <div className="flex items-start gap-2 px-3 py-2 text-xs text-danger">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}
      {output && (
        <pre className="max-h-40 overflow-auto px-3 py-2 text-xs whitespace-pre-wrap text-foreground">
          {output}
        </pre>
      )}
    </div>
  );
}

// -- Data Table --------------------------------------------------------------

function DataTable({
  rows,
  total,
  label,
}: {
  rows: Array<Record<string, unknown>>;
  total?: number;
  label?: string;
}) {
  const [expanded, setExpanded] = useState(true);
  if (rows.length === 0) {
    return (
      <div className="rounded-md border border-border-subtle bg-elevated px-3 py-2 text-xs text-secondary">
        <TableIcon className="mr-1.5 inline h-3 w-3" />
        {label ?? "查询"} · 无数据
      </div>
    );
  }

  const columns = Object.keys(rows[0]);
  const displayRows = rows.slice(0, 50);
  const truncated = rows.length > 50;
  const totalCount = total ?? rows.length;

  return (
    <div className="rounded-md border border-border-subtle bg-elevated">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center gap-1.5 border-b border-border-subtle px-3 py-1.5 text-left text-xs font-medium text-secondary"
      >
        {expanded ? (
          <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronRight className="h-3 w-3" />
        )}
        <TableIcon className="h-3 w-3" />
        {label ?? "查询结果"} · {totalCount} 行
        {totalCount !== rows.length && (
          <span className="text-tertiary">（返回 {rows.length}）</span>
        )}
      </button>
      {expanded && (
        <div className="max-h-60 overflow-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-subtle">
              <tr>
                {columns.map((col) => (
                  <th
                    key={col}
                    className="whitespace-nowrap px-3 py-1.5 text-left font-medium text-secondary"
                  >
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {displayRows.map((row, i) => (
                <tr
                  key={i}
                  className="border-t border-border-subtle"
                >
                  {columns.map((col) => (
                    <td
                      key={col}
                      className="whitespace-nowrap px-3 py-1.5 text-foreground"
                    >
                      {formatCell(row[col])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {truncated && (
            <div className="border-t border-border-subtle px-3 py-1.5 text-center text-xs text-tertiary">
              共 {totalCount} 行，已截断显示前 50 行
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// -- Resource List -----------------------------------------------------------

function ResourceList({
  resources,
  label,
}: {
  resources: Array<Record<string, unknown>>;
  label?: string;
}) {
  return (
    <div className="rounded-md border border-border-subtle bg-elevated">
      <div className="flex items-center gap-1.5 border-b border-border-subtle px-3 py-1.5 text-xs font-medium text-secondary">
        <ListIcon className="h-3 w-3" />
        {label ?? "资源列表"} · {resources.length} 个
      </div>
      <div className="flex flex-wrap gap-1.5 p-2">
        {resources.map((res, i) => (
          <span
            key={i}
            className="inline-flex items-center gap-1 rounded-md bg-subtle px-2 py-1 text-xs text-foreground"
          >
            <span className="font-medium">
              {String(res.display_name ?? res.name ?? `资源 ${i + 1}`)}
            </span>
            {typeof res.access_mode === "string" && (
              <span className="text-tertiary">{res.access_mode}</span>
            )}
          </span>
        ))}
      </div>
    </div>
  );
}

// -- Schema Table ------------------------------------------------------------

function SchemaTable({
  columns,
  tableName,
  label,
}: {
  columns: Array<Record<string, unknown>>;
  tableName?: string;
  label?: string;
}) {
  return (
    <div className="rounded-md border border-border-subtle bg-elevated">
      <div className="flex items-center gap-1.5 border-b border-border-subtle px-3 py-1.5 text-xs font-medium text-secondary">
        <DatabaseIcon className="h-3 w-3" />
        {label ?? "Schema"}
        {tableName && (
          <span className="text-tertiary">· {tableName}</span>
        )}
      </div>
      <div className="max-h-60 overflow-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-subtle">
            <tr>
              <th className="px-3 py-1.5 text-left font-medium text-secondary">列名</th>
              <th className="px-3 py-1.5 text-left font-medium text-secondary">类型</th>
            </tr>
          </thead>
          <tbody>
            {columns.map((col, i) => (
              <tr key={i} className="border-t border-border-subtle">
                <td className="whitespace-nowrap px-3 py-1.5 font-medium text-foreground">
                  {String(col.name ?? col.column_name ?? "")}
                </td>
                <td className="whitespace-nowrap px-3 py-1.5 text-secondary">
                  {String(col.type ?? col.data_type ?? "")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// -- Helpers -----------------------------------------------------------------

function SimpleText({ label, text }: { label?: string; text: string }) {
  return (
    <div className="rounded-md border border-border-subtle bg-elevated px-3 py-2">
      {label && (
        <div className="mb-1 text-xs font-medium text-secondary">{label}</div>
      )}
      <pre className="max-h-40 overflow-auto text-xs whitespace-pre-wrap text-foreground">
        {text}
      </pre>
    </div>
  );
}

function FallbackJson({ value }: { value: string | null }) {
  const [open, setOpen] = useState(false);
  if (!value) return null;
  return (
    <div className="rounded-md border border-border-subtle bg-elevated">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 px-3 py-1.5 text-left text-xs font-medium text-secondary"
      >
        {open ? (
          <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronRight className="h-3 w-3" />
        )}
        详情
      </button>
      {open && (
        <pre className="max-h-40 overflow-auto border-t border-border-subtle px-3 py-2 font-mono text-xs whitespace-pre-wrap text-foreground">
          {value}
        </pre>
      )}
    </div>
  );
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
