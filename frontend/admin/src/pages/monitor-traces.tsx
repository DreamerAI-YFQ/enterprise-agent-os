import { useState, useMemo, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import { Spinner, cn } from "@eaos/shared";
import { relativeTime } from "../lib/relative-time";
import { GitBranch, Search, Clock, ChevronRight, ChevronDown, Circle } from "lucide-react";

interface Span {
  id: string;
  tenant_id: string;
  trace_id: string;
  parent_span_id: string | null;
  agent_id: string;
  session_id: string;
  user_id: string;
  granularity: string;
  name: string;
  start_time: string;
  end_time: string;
  duration_ms: number;
  status: string;
  attributes: Record<string, unknown>;
  events: { name: string; timestamp: string; attributes: Record<string, unknown> }[];
  cost_tokens: number;
  cost_usd: number;
}

interface TreeNode {
  span: Span;
  children: TreeNode[];
  depth: number;
}

function buildTree(spans: Span[]): TreeNode[] {
  const map = new Map<string, TreeNode>();
  const roots: TreeNode[] = [];

  const sorted = [...spans].sort(
    (a, b) => new Date(a.start_time).getTime() - new Date(b.start_time).getTime()
  );

  for (const span of sorted) {
    map.set(span.id, { span, children: [], depth: 0 });
  }

  for (const span of sorted) {
    const node = map.get(span.id)!;
    if (span.parent_span_id && map.has(span.parent_span_id)) {
      const parent = map.get(span.parent_span_id)!;
      node.depth = parent.depth + 1;
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  }

  return roots;
}

function flattenTree(nodes: TreeNode[], expanded: Set<string>): { node: TreeNode; hasChildren: boolean }[] {
  const result: { node: TreeNode; hasChildren: boolean }[] = [];
  const walk = (nodes: TreeNode[]) => {
    for (const node of nodes) {
      const hasChildren = node.children.length > 0;
      result.push({ node, hasChildren });
      if (hasChildren && expanded.has(node.span.id)) {
        walk(node.children);
      }
    }
  };
  walk(nodes);
  return result;
}

const GRANULARITY_COLORS: Record<string, string> = {
  session: "bg-accent",
  task: "bg-violet-500",
  call: "bg-blue-500",
  tool: "bg-emerald-500",
};

const STATUS_COLORS: Record<string, string> = {
  ok: "text-success",
  error: "text-danger",
  timeout: "text-warning",
};

export default function MonitorTracesPage() {
  const [traceId, setTraceId] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selectedSpan, setSelectedSpan] = useState<Span | null>(null);

  // Read trace_id from URL hash
  useEffect(() => {
    const hash = window.location.hash;
    const match = hash.match(/trace_id=([^&]+)/);
    if (match) {
      setTraceId(decodeURIComponent(match[1]));
      setSubmitted(decodeURIComponent(match[1]));
    }
  }, []);

  const traceQuery = useQuery({
    queryKey: ["admin", "spans", "trace", submitted],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/spans/trace/{trace_id}", {
        params: { path: { trace_id: submitted } },
      });
      if (error || !data) return [] as Span[];
      return data as unknown as Span[];
    },
    enabled: submitted.length > 0,
  });

  const spans = traceQuery.data ?? [];
  const tree = useMemo(() => buildTree(spans), [spans]);

  const totalDuration = useMemo(() => {
    if (spans.length === 0) return 0;
    const starts = spans.map((s) => new Date(s.start_time).getTime());
    const ends = spans.map((s) => new Date(s.end_time).getTime());
    return Math.max(...ends) - Math.min(...starts);
  }, [spans]);

  const minStart = useMemo(() => {
    if (spans.length === 0) return 0;
    return Math.min(...spans.map((s) => new Date(s.start_time).getTime()));
  }, [spans]);

  const flattened = useMemo(() => {
    if (tree.length > 0 && expanded.size === 0) {
      setExpanded(new Set([tree[0].span.id]));
    }
    return flattenTree(tree, expanded);
  }, [tree, expanded]);

  const toggleExpand = (id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (traceId.trim()) {
      setSubmitted(traceId.trim());
      setSelectedSpan(null);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <h1 className="text-2xl font-semibold text-foreground">链路追踪</h1>
        <p className="mt-1 text-sm text-secondary">
          查看 Trace 的 Span 树形结构、耗时与事件
        </p>
      </div>

      <div className="border-b border-border-subtle px-8 py-3">
        <form onSubmit={handleSubmit} className="flex items-center gap-2">
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-tertiary" />
            <input
              type="text"
              placeholder="输入 Trace ID 或 Session ID..."
              value={traceId}
              onChange={(e) => setTraceId(e.target.value)}
              className="w-full rounded-md border border-border bg-elevated py-2 pl-9 pr-3 text-sm text-foreground placeholder:text-tertiary focus:border-accent focus:outline-none"
            />
          </div>
          <button
            type="submit"
            disabled={!traceId.trim() || traceQuery.isFetching}
            className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-accent-strong disabled:opacity-50"
          >
            {traceQuery.isFetching ? "查询中..." : "查询"}
          </button>
        </form>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Span Tree */}
        <div className="flex-1 overflow-y-auto">
          {!submitted ? (
            <div className="flex h-full items-center justify-center p-8">
              <div className="flex flex-col items-center gap-3 text-center">
                <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                  <GitBranch className="h-8 w-8" strokeWidth={1.5} />
                </div>
                <h3 className="text-lg font-medium text-foreground">输入 Trace ID</h3>
                <p className="max-w-sm text-sm text-secondary">
                  从执行监控页面的任务点击"链路"跳转，或直接输入 Trace ID
                </p>
              </div>
            </div>
          ) : traceQuery.isLoading ? (
            <div className="flex h-40 items-center justify-center">
              <Spinner />
            </div>
          ) : spans.length === 0 ? (
            <div className="flex h-full items-center justify-center p-8">
              <div className="flex flex-col items-center gap-3 text-center">
                <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                  <GitBranch className="h-8 w-8" strokeWidth={1.5} />
                </div>
                <h3 className="text-lg font-medium text-foreground">无 Span 数据</h3>
                <p className="max-w-sm text-sm text-secondary">
                  Trace ID 不存在或该会话无链路记录
                </p>
              </div>
            </div>
          ) : (
            <div className="px-8 py-4">
              {/* Trace Summary */}
              <div className="mb-4 flex items-center gap-4 text-xs text-tertiary">
                <span>Trace: {submitted.slice(0, 16)}...</span>
                <span>·</span>
                <span>{spans.length} 个 Span</span>
                <span>·</span>
                <span>总耗时 {totalDuration}ms</span>
              </div>

              {/* Flame Graph Header */}
              <div className="mb-2 flex items-center gap-2 text-xs font-medium text-secondary">
                <Clock className="h-3.5 w-3.5" />
                耗时分布
              </div>

              {/* Span List */}
              <div className="space-y-0.5">
                {flattened.map(({ node, hasChildren }) => {
                  const span = node.span;
                  const startTime = new Date(span.start_time).getTime();
                  const leftOffset = ((startTime - minStart) / totalDuration) * 100;
                  const widthPct = Math.max((span.duration_ms / totalDuration) * 100, 1);
                  const isExpanded = expanded.has(span.id);
                  const isSelected = selectedSpan?.id === span.id;

                  return (
                    <div
                      key={span.id}
                      className={cn(
                        "flex items-center gap-2 rounded-md px-2 py-1.5 transition-colors",
                        isSelected ? "bg-accent-subtle/30" : "hover:bg-subtle/40"
                      )}
                    >
                      {/* Expand/Collapse */}
                      <button
                        onClick={() => hasChildren && toggleExpand(span.id)}
                        className="flex w-5 items-center justify-center"
                        style={{ marginLeft: `${node.depth * 16}px` }}
                      >
                        {hasChildren ? (
                          isExpanded ? (
                            <ChevronDown className="h-3.5 w-3.5 text-tertiary" />
                          ) : (
                            <ChevronRight className="h-3.5 w-3.5 text-tertiary" />
                          )
                        ) : (
                          <Circle className="h-1.5 w-1.5 text-tertiary" />
                        )}
                      </button>

                      {/* Name + Granularity Badge */}
                      <button
                        onClick={() => setSelectedSpan(span)}
                        className="flex min-w-0 flex-1 items-center gap-2"
                      >
                        <div
                          className={cn(
                            "h-2 w-2 shrink-0 rounded-full",
                            GRANULARITY_COLORS[span.granularity] ?? "bg-gray-400"
                          )}
                        />
                        <span className="truncate text-xs text-foreground">
                          {span.name}
                        </span>
                        <span className="shrink-0 rounded bg-subtle px-1.5 py-0.5 text-xs text-tertiary">
                          {span.granularity}
                        </span>
                      </button>

                      {/* Flame Bar */}
                      <div className="relative h-5 w-64 overflow-hidden rounded bg-subtle/30">
                        <div
                          className={cn(
                            "absolute h-full rounded",
                            GRANULARITY_COLORS[span.granularity] ?? "bg-gray-400",
                            span.status === "error" && "bg-danger",
                            span.status === "timeout" && "bg-warning"
                          )}
                          style={{
                            left: `${leftOffset}%`,
                            width: `${widthPct}%`,
                            opacity: 0.8,
                          }}
                        />
                      </div>

                      {/* Duration + Status */}
                      <span className="w-20 text-right font-mono text-xs text-foreground">
                        {span.duration_ms}ms
                      </span>
                      <span
                        className={cn(
                          "w-16 text-xs font-medium",
                          STATUS_COLORS[span.status] ?? "text-tertiary"
                        )}
                      >
                        {span.status}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* Span Detail Panel */}
        {selectedSpan && (
          <div className="flex w-80 shrink-0 flex-col border-l border-border-subtle overflow-y-auto">
            <div className="border-b border-border-subtle px-4 py-3">
              <h3 className="text-sm font-medium text-foreground">
                {selectedSpan.name}
              </h3>
              <p className="mt-1 font-mono text-xs text-tertiary">
                {selectedSpan.id.slice(0, 16)}
              </p>
            </div>

            <div className="flex-1 space-y-4 px-4 py-4">
              {/* Basic Info */}
              <div className="space-y-1.5">
                <div className="flex justify-between text-xs">
                  <span className="text-tertiary">类型</span>
                  <span className="text-foreground">{selectedSpan.granularity}</span>
                </div>
                <div className="flex justify-between text-xs">
                  <span className="text-tertiary">状态</span>
                  <span className={STATUS_COLORS[selectedSpan.status] ?? "text-tertiary"}>
                    {selectedSpan.status}
                  </span>
                </div>
                <div className="flex justify-between text-xs">
                  <span className="text-tertiary">耗时</span>
                  <span className="font-mono text-foreground">{selectedSpan.duration_ms}ms</span>
                </div>
                <div className="flex justify-between text-xs">
                  <span className="text-tertiary">开始</span>
                  <span className="text-foreground">{relativeTime(selectedSpan.start_time)}</span>
                </div>
                <div className="flex justify-between text-xs">
                  <span className="text-tertiary">Agent</span>
                  <span className="font-mono text-foreground">{selectedSpan.agent_id.slice(0, 8)}</span>
                </div>
                <div className="flex justify-between text-xs">
                  <span className="text-tertiary">Token</span>
                  <span className="text-foreground">{selectedSpan.cost_tokens}</span>
                </div>
                <div className="flex justify-between text-xs">
                  <span className="text-tertiary">成本</span>
                  <span className="text-foreground">${selectedSpan.cost_usd.toFixed(6)}</span>
                </div>
              </div>

              {/* Attributes */}
              {Object.keys(selectedSpan.attributes).length > 0 && (
                <div>
                  <p className="mb-1.5 text-xs font-medium text-secondary">属性</p>
                  <div className="rounded-md border border-border bg-subtle/30 p-2">
                    {Object.entries(selectedSpan.attributes).map(([k, v]) => (
                      <div key={k} className="flex justify-between py-0.5 text-xs">
                        <span className="text-tertiary">{k}</span>
                        <span className="ml-2 truncate font-mono text-foreground">
                          {typeof v === "string" ? v : JSON.stringify(v)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Events */}
              {selectedSpan.events.length > 0 && (
                <div>
                  <p className="mb-1.5 text-xs font-medium text-secondary">
                    事件 ({selectedSpan.events.length})
                  </p>
                  <div className="space-y-1.5">
                    {selectedSpan.events.map((evt, idx) => (
                      <div
                        key={idx}
                        className="rounded-md border border-border-subtle bg-subtle/20 p-2"
                      >
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-medium text-foreground">
                            {evt.name}
                          </span>
                          <span className="text-xs text-tertiary">
                            {relativeTime(evt.timestamp)}
                          </span>
                        </div>
                        {Object.keys(evt.attributes).length > 0 && (
                          <pre className="mt-1 overflow-x-auto text-xs text-tertiary">
                            {JSON.stringify(evt.attributes, null, 2)}
                          </pre>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
