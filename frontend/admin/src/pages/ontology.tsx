import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import { Button, Spinner, cn, toast } from "@eaos/shared";
import { Network, Plus, X, Trash2, AlertCircle, ChevronRight, ChevronDown, ArrowRight } from "lucide-react";

interface Term {
  id: string;
  ontology_id: string;
  node_type: string;
  name: string;
  parent_id: string | null;
  properties: Record<string, unknown>;
}

interface GraphNode {
  id: string;
  ontology_id: string;
  node_type: string;
  name: string;
  parent_id: string | null;
  properties: Record<string, unknown>;
}

interface GraphEdge {
  id: string;
  source: string;
  target: string;
  label: string;
}

interface OntologyGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

const TYPE_LABELS: Record<string, string> = {
  object: "对象",
  attribute: "属性",
  relation: "关系",
  rule: "规则",
  code: "代码",
};

const TYPE_COLORS: Record<string, string> = {
  object: "#3b82f6",
  attribute: "#10b981",
  relation: "#f59e0b",
  rule: "#8b5cf6",
  code: "#6b7280",
};

type ViewMode = "list" | "graph";

export default function OntologyPage() {
  const queryClient = useQueryClient();
  const [viewMode, setViewMode] = useState<ViewMode>("list");
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [showCreate, setShowCreate] = useState(false);
  const [showGaps, setShowGaps] = useState(false);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["admin", "ontology", "terms"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/ontology/terms", {});
      if (error || !data) return [] as Term[];
      return data as unknown as Term[];
    },
  });

  const graphQuery = useQuery({
    queryKey: ["admin", "ontology", "graph"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/ontology/graph", {});
      if (error || !data) return { nodes: [], edges: [] } as OntologyGraph;
      return data as unknown as OntologyGraph;
    },
    enabled: viewMode === "graph",
  });

  const gapsQuery = useQuery({
    queryKey: ["admin", "ontology", "gaps"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/admin/ontology/gaps", {});
      if (error || !data) return [] as Term[];
      return data as unknown as Term[];
    },
    enabled: showGaps,
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiClient.DELETE("/admin/ontology/terms/{term_id}", {
        params: { path: { term_id: id } },
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ["admin", "ontology"],
      });
      toast.show({ title: "术语已删除", variant: "success" });
    },
  });

  const terms = useMemo(() => query.data ?? [], [query.data]);
  const filteredTerms = useMemo(() => {
    if (typeFilter === "all") return terms;
    return terms.filter((t) => t.node_type === typeFilter);
  }, [terms, typeFilter]);

  const availableTypes = useMemo(() => {
    const types = new Set(terms.map((t) => t.node_type));
    return Array.from(types);
  }, [terms]);

  const gaps = gapsQuery.data ?? [];

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">本体管理</h1>
            <p className="mt-1 text-sm text-secondary">
              管理企业知识本体的术语和关系
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setShowGaps((v) => !v)}
            >
              <AlertCircle className="h-3.5 w-3.5" />
              知识缺口
            </Button>
            <Button size="sm" onClick={() => setShowCreate(true)}>
              <Plus className="h-4 w-4" />
              创建术语
            </Button>
          </div>
        </div>
      </div>

      {showGaps && (
        <div className="border-b border-warning/30 bg-warning/5 px-8 py-3">
          <p className="text-xs font-medium text-warning">
            知识缺口（{gaps.length} 个术语缺少属性定义）
          </p>
          {gaps.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {gaps.slice(0, 10).map((g) => (
                <span
                  key={g.id}
                  className="rounded bg-warning/10 px-1.5 py-0.5 text-xs text-warning"
                >
                  {g.name} ({TYPE_LABELS[g.node_type] ?? g.node_type})
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="border-b border-border-subtle px-8 py-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1 rounded-md bg-subtle p-0.5">
            <button
              type="button"
              onClick={() => setViewMode("list")}
              className={cn(
                "rounded-sm px-3 py-1 text-xs font-medium transition-colors",
                viewMode === "list"
                  ? "bg-elevated text-foreground shadow-sm"
                  : "text-secondary hover:text-foreground",
              )}
            >
              列表视图
            </button>
            <button
              type="button"
              onClick={() => setViewMode("graph")}
              className={cn(
                "rounded-sm px-3 py-1 text-xs font-medium transition-colors",
                viewMode === "graph"
                  ? "bg-elevated text-foreground shadow-sm"
                  : "text-secondary hover:text-foreground",
              )}
            >
              关系视图
            </button>
          </div>

          {viewMode === "list" && (
            <div className="flex items-center gap-1 rounded-md bg-subtle p-0.5">
              <button
                type="button"
                onClick={() => setTypeFilter("all")}
                className={cn(
                  "rounded-sm px-2.5 py-1 text-xs font-medium transition-colors",
                  typeFilter === "all"
                    ? "bg-elevated text-foreground shadow-sm"
                    : "text-secondary hover:text-foreground",
                )}
              >
                全部 ({terms.length})
              </button>
              {availableTypes.map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setTypeFilter(t)}
                  className={cn(
                    "rounded-sm px-2.5 py-1 text-xs font-medium transition-colors",
                    typeFilter === t
                      ? "bg-elevated text-foreground shadow-sm"
                      : "text-secondary hover:text-foreground",
                  )}
                >
                  {TYPE_LABELS[t] ?? t} (
                  {terms.filter((x) => x.node_type === t).length})
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-hidden px-8 pb-8 pt-4">
        {viewMode === "list" ? (
          <ListView
            isLoading={query.isLoading}
            terms={filteredTerms}
            onDelete={(id) => deleteMutation.mutate(id)}
            deletePending={deleteMutation.isPending}
          />
        ) : (
          <RelationView
            isLoading={graphQuery.isLoading}
            data={graphQuery.data}
            selectedNodeId={selectedNodeId}
            onSelectNode={setSelectedNodeId}
          />
        )}
      </div>

      {showCreate && (
        <CreateTermModal
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            setShowCreate(false);
            void queryClient.invalidateQueries({
              queryKey: ["admin", "ontology"],
            });
          }}
        />
      )}
    </div>
  );
}

function ListView({
  isLoading,
  terms,
  onDelete,
  deletePending,
}: {
  isLoading: boolean;
  terms: Term[];
  onDelete: (id: string) => void;
  deletePending: boolean;
}) {
  if (isLoading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <Spinner />
      </div>
    );
  }

  if (terms.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <div className="flex flex-col items-center gap-3 text-center">
          <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
            <Network className="h-8 w-8" strokeWidth={1.5} />
          </div>
          <h3 className="text-2xl font-semibold text-foreground">暂无术语</h3>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full space-y-2 overflow-y-auto">
      {terms.map((term) => (
        <div
          key={term.id}
          className="flex items-center gap-3 rounded-md border border-border bg-elevated p-3 shadow-sm"
        >
          <span
            className="shrink-0 rounded-full px-2 py-0.5 text-xs"
            style={{
              backgroundColor: `${TYPE_COLORS[term.node_type] ?? "#6b7280"}1a`,
              color: TYPE_COLORS[term.node_type] ?? "#6b7280",
            }}
          >
            {TYPE_LABELS[term.node_type] ?? term.node_type}
          </span>
          <span className="flex-1 text-sm text-foreground">{term.name}</span>
          {Object.keys(term.properties).length > 0 && (
            <span className="text-xs text-tertiary">
              {Object.keys(term.properties).length} 个属性
            </span>
          )}
          <button
            type="button"
            onClick={() => onDelete(term.id)}
            disabled={deletePending}
            className="rounded p-1 text-tertiary hover:bg-subtle hover:text-danger"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}

/** Relation view: structured table showing nodes and their edges. */
function RelationView({
  isLoading,
  data,
  selectedNodeId,
  onSelectNode,
}: {
  isLoading: boolean;
  data?: OntologyGraph;
  selectedNodeId: string | null;
  onSelectNode: (id: string | null) => void;
}) {
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
      </div>
    );
  }

  if (!data || data.nodes.length === 0) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <div className="flex flex-col items-center gap-3 text-center">
          <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
            <Network className="h-8 w-8" strokeWidth={1.5} />
          </div>
          <h3 className="text-2xl font-semibold text-foreground">暂无节点</h3>
          <p className="max-w-sm text-sm text-secondary">
            创建本体术语后将在此显示关系
          </p>
        </div>
      </div>
    );
  }

  const nodeById = new Map(data.nodes.map((n) => [n.id, n]));

  // Build outgoing + incoming edge maps for each node.
  const outEdges = new Map<string, GraphEdge[]>();
  const inEdges = new Map<string, GraphEdge[]>();
  for (const e of data.edges) {
    if (!outEdges.has(e.source)) outEdges.set(e.source, []);
    if (!inEdges.has(e.target)) inEdges.set(e.target, []);
    outEdges.get(e.source)!.push(e);
    inEdges.get(e.target)!.push(e);
  }

  const typeCounts = data.nodes.reduce<Record<string, number>>((acc, n) => {
    acc[n.node_type] = (acc[n.node_type] ?? 0) + 1;
    return acc;
  }, {});

  const filteredNodes =
    typeFilter === "all"
      ? data.nodes
      : data.nodes.filter((n) => n.node_type === typeFilter);

  const selectedNode = selectedNodeId ? nodeById.get(selectedNodeId) : undefined;

  return (
    <div className="flex h-full gap-4">
      {/* Left: Node table */}
      <div className={`flex flex-col ${selectedNode ? "w-3/5" : "flex-1"}`}>
        {/* Stats bar */}
        <div className="mb-3 flex flex-wrap items-center gap-3 text-xs">
          <span className="text-tertiary">
            共 {data.nodes.length} 个节点 / {data.edges.length} 条边
          </span>
          <span>·</span>
          {Object.entries(typeCounts).map(([type, count]) => (
            <button
              key={type}
              type="button"
              onClick={() =>
                setTypeFilter(typeFilter === type ? "all" : type)
              }
              className={cn(
                "flex items-center gap-1.5 rounded-full px-2 py-0.5 transition-colors",
                typeFilter === type
                  ? "ring-1 ring-accent"
                  : "hover:bg-subtle",
              )}
            >
              <span
                className="h-2 w-2 rounded-full"
                style={{ backgroundColor: TYPE_COLORS[type] ?? "#6b7280" }}
              />
              {TYPE_LABELS[type] ?? type}: {count}
            </button>
          ))}
        </div>

        {/* Table */}
        <div className="flex-1 overflow-y-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10 border-b border-border bg-subtle/80 backdrop-blur-sm">
              <tr>
                <th className="px-3 py-2 text-left text-xs font-medium text-tertiary">名称</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-tertiary">类型</th>
                <th className="px-3 py-2 text-left text-xs font-medium text-tertiary">父节点</th>
                <th className="px-3 py-2 text-center text-xs font-medium text-tertiary">出边</th>
                <th className="px-3 py-2 text-center text-xs font-medium text-tertiary">入边</th>
                <th className="px-3 py-2 text-center text-xs font-medium text-tertiary">属性</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border-subtle">
              {filteredNodes.map((node) => {
                const isSelected = node.id === selectedNodeId;
                const isExpanded = node.id === expandedId;
                const outE = outEdges.get(node.id) ?? [];
                const inE = inEdges.get(node.id) ?? [];
                const propCount = Object.keys(node.properties).length;
                const parentNode = node.parent_id
                  ? nodeById.get(node.parent_id)
                  : undefined;

                return (
                  <tbody key={node.id}>
                    <tr
                      className={cn(
                        "cursor-pointer transition-colors",
                        isSelected
                          ? "bg-accent/5"
                          : "hover:bg-subtle/50",
                      )}
                      onClick={() => {
                        onSelectNode(isSelected ? null : node.id);
                        setExpandedId(isExpanded ? null : node.id);
                      }}
                    >
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-1.5">
                          {outE.length + inE.length > 0 && (
                            isExpanded ? (
                              <ChevronDown className="h-3 w-3 text-tertiary" />
                            ) : (
                              <ChevronRight className="h-3 w-3 text-tertiary" />
                            )
                          )}
                          <span className="font-medium text-foreground">
                            {node.name}
                          </span>
                        </div>
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className="rounded-full px-2 py-0.5 text-xs"
                          style={{
                            backgroundColor: `${TYPE_COLORS[node.node_type] ?? "#6b7280"}1a`,
                            color: TYPE_COLORS[node.node_type] ?? "#6b7280",
                          }}
                        >
                          {TYPE_LABELS[node.node_type] ?? node.node_type}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-xs text-secondary">
                        {parentNode ? parentNode.name : "—"}
                      </td>
                      <td className="px-3 py-2 text-center text-xs text-tertiary">
                        {outE.length > 0 ? outE.length : "—"}
                      </td>
                      <td className="px-3 py-2 text-center text-xs text-tertiary">
                        {inE.length > 0 ? inE.length : "—"}
                      </td>
                      <td className="px-3 py-2 text-center text-xs text-tertiary">
                        {propCount > 0 ? propCount : "—"}
                      </td>
                    </tr>

                    {/* Expanded: show edges inline */}
                    {isExpanded && (outE.length + inE.length > 0) && (
                      <tr className="bg-subtle/30">
                        <td colSpan={6} className="px-6 py-2">
                          <div className="space-y-1 text-xs">
                            {outE.map((e) => {
                              const target = nodeById.get(e.target);
                              return (
                                <div key={e.id} className="flex items-center gap-2">
                                  <span className="font-medium text-foreground">{node.name}</span>
                                  <ArrowRight className="h-3 w-3 text-accent" />
                                  <span className="rounded bg-accent/10 px-1 text-accent">
                                    {e.label}
                                  </span>
                                  <ArrowRight className="h-3 w-3 text-accent" />
                                  <span className="font-medium text-foreground">
                                    {target?.name ?? e.target.slice(0, 8)}
                                  </span>
                                </div>
                              );
                            })}
                            {inE.map((e) => {
                              const source = nodeById.get(e.source);
                              return (
                                <div key={e.id} className="flex items-center gap-2">
                                  <span className="font-medium text-foreground">
                                    {source?.name ?? e.source.slice(0, 8)}
                                  </span>
                                  <ArrowRight className="h-3 w-3 text-secondary" />
                                  <span className="rounded bg-subtle px-1 text-secondary">
                                    {e.label}
                                  </span>
                                  <ArrowRight className="h-3 w-3 text-secondary" />
                                  <span className="font-medium text-foreground">{node.name}</span>
                                </div>
                              );
                            })}
                          </div>
                        </td>
                      </tr>
                    )}
                  </tbody>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Right: Detail panel */}
      {selectedNode && (
        <div className="w-2/5 overflow-y-auto rounded-md border border-border bg-elevated p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <span
                className="rounded-full px-2 py-0.5 text-xs"
                style={{
                  backgroundColor: `${TYPE_COLORS[selectedNode.node_type] ?? "#6b7280"}1a`,
                  color: TYPE_COLORS[selectedNode.node_type] ?? "#6b7280",
                }}
              >
                {TYPE_LABELS[selectedNode.node_type] ?? selectedNode.node_type}
              </span>
              <span className="font-semibold text-foreground">{selectedNode.name}</span>
            </div>
            <button
              type="button"
              onClick={() => onSelectNode(null)}
              className="rounded p-1 text-tertiary hover:bg-subtle hover:text-foreground"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* Properties */}
          {Object.keys(selectedNode.properties).length > 0 && (
            <div className="mb-4">
              <h4 className="mb-2 text-xs font-medium text-tertiary">属性</h4>
              <table className="w-full text-xs">
                <tbody className="divide-y divide-border-subtle">
                  {Object.entries(selectedNode.properties).map(([key, val]) => (
                    <tr key={key}>
                      <td className="py-1.5 pr-3 font-medium text-secondary">{key}</td>
                      <td className="py-1.5 text-foreground">
                        {typeof val === "object" ? JSON.stringify(val) : String(val)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Parent */}
          {selectedNode.parent_id && (
            <div className="mb-4">
              <h4 className="mb-2 text-xs font-medium text-tertiary">父节点</h4>
              <button
                type="button"
                onClick={() => onSelectNode(selectedNode.parent_id!)}
                className="rounded px-2 py-1 text-sm text-accent hover:bg-accent/10"
              >
                {nodeById.get(selectedNode.parent_id)?.name ?? selectedNode.parent_id.slice(0, 8)}
              </button>
            </div>
          )}

          {/* Outgoing edges */}
          {(() => {
            const outE = outEdges.get(selectedNode.id) ?? [];
            if (outE.length === 0) return null;
            return (
              <div className="mb-4">
                <h4 className="mb-2 text-xs font-medium text-tertiary">
                  出边 ({outE.length})
                </h4>
                <div className="space-y-1">
                  {outE.map((e) => {
                    const target = nodeById.get(e.target);
                    return (
                      <button
                        key={e.id}
                        type="button"
                        onClick={() => onSelectNode(e.target)}
                        className="flex w-full items-center gap-2 rounded px-2 py-1 text-xs hover:bg-subtle"
                      >
                        <span className="rounded bg-accent/10 px-1 text-accent">{e.label}</span>
                        <ArrowRight className="h-3 w-3 text-tertiary" />
                        <span className="text-foreground">{target?.name ?? e.target.slice(0, 8)}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })()}

          {/* Incoming edges */}
          {(() => {
            const inE = inEdges.get(selectedNode.id) ?? [];
            if (inE.length === 0) return null;
            return (
              <div className="mb-4">
                <h4 className="mb-2 text-xs font-medium text-tertiary">
                  入边 ({inE.length})
                </h4>
                <div className="space-y-1">
                  {inE.map((e) => {
                    const source = nodeById.get(e.source);
                    return (
                      <button
                        key={e.id}
                        type="button"
                        onClick={() => onSelectNode(e.source)}
                        className="flex w-full items-center gap-2 rounded px-2 py-1 text-xs hover:bg-subtle"
                      >
                        <span className="text-foreground">{source?.name ?? e.source.slice(0, 8)}</span>
                        <ArrowRight className="h-3 w-3 text-tertiary" />
                        <span className="rounded bg-subtle px-1 text-secondary">{e.label}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })()}
        </div>
      )}
    </div>
  );
}

function CreateTermModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [nodeType, setNodeType] = useState("object");

  const createMutation = useMutation({
    mutationFn: async () => {
      await apiClient.POST("/admin/ontology/terms", {
        body: { node_type: nodeType, name, properties: {} },
      });
    },
    onSuccess: () => {
      toast.show({ title: "术语已创建", variant: "success" });
      onCreated();
    },
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 backdrop-blur-sm">
      <div className="w-full max-w-md rounded-lg bg-elevated p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-foreground">创建术语</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-tertiary hover:bg-subtle hover:text-foreground"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            createMutation.mutate();
          }}
          className="space-y-3"
        >
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary">
              名称
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:border-accent focus:outline-none"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-secondary">
              类型
            </label>
            <select
              value={nodeType}
              onChange={(e) => setNodeType(e.target.value)}
              className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none"
            >
              {Object.entries(TYPE_LABELS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="ghost" type="button" onClick={onClose}>
              取消
            </Button>
            <Button type="submit" disabled={createMutation.isPending}>
              {createMutation.isPending ? "创建中..." : "创建"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
