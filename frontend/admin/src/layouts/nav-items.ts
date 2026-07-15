import {
  LayoutDashboard,
  Bot,
  ShieldCheck,
  Workflow,
  Sparkles,
  FileText,
  Network,
  GitBranch,
  Database,
  Terminal,
  BarChart3,
  Activity,
  Gauge,
  LineChart,
  Cpu,
  Plug,
  Zap,
  Users,
  FlaskConical,
  Puzzle,
  Settings,
  Building2,
  Brain,
  ScrollText,
  PackageOpen,
  Shield,
  Globe2,
  KeyRound,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  /** i18n key under the `nav.*` namespace, e.g. "nav.dashboard". */
  labelKey: string;
  /** Fallback label used when i18n is not yet initialised. */
  label: string;
  to: string;
  icon: LucideIcon;
  /** Roles allowed to see this item. Undefined = visible to all authenticated users. */
  roles?: string[];
}

export interface NavGroup {
  /** i18n key under the `nav.*` namespace, e.g. "nav.overview". */
  labelKey: string;
  /** Fallback label used when i18n is not yet initialised. */
  label: string;
  items: NavItem[];
  /** Roles allowed to see this group. Undefined = visible to all. */
  roles?: string[];
}

/**
 * Role hierarchy:
 * - admin:  full access (config center, user management, etc.)
 * - analyst: read-only access (BI, monitoring, knowledge) — no config mutations
 * - viewer:  dashboard + monitoring only
 */
export const navGroups: NavGroup[] = [
  {
    labelKey: "nav.overview",
    label: "总览",
    items: [{ labelKey: "nav.dashboard", label: "仪表盘", to: "/admin", icon: LayoutDashboard }],
  },
  {
    labelKey: "nav.businessCenter",
    label: "业务中心",
    items: [
      { labelKey: "nav.agents", label: "Agent 管理", to: "/admin/agents", icon: Bot, roles: ["admin"] },
      { labelKey: "nav.approvals", label: "审批管理", to: "/admin/approvals", icon: ShieldCheck, roles: ["admin"] },
      { labelKey: "nav.workflows", label: "工作流", to: "/admin/workflows", icon: Workflow, roles: ["admin"] },
    ],
    roles: ["admin"],
  },
  {
    labelKey: "nav.biCenter",
    label: "BI 中心",
    items: [
      { labelKey: "nav.biData", label: "数据浏览", to: "/admin/bi/data", icon: Database },
      { labelKey: "nav.biSql", label: "SQL 控制台", to: "/admin/bi/sql", icon: Terminal, roles: ["admin"] },
      { labelKey: "nav.biQuery", label: "自然语言查询", to: "/admin/bi/query", icon: BarChart3 },
      { labelKey: "nav.biMetrics", label: "指标中心", to: "/admin/bi/metrics", icon: Gauge },
    ],
  },
  {
    labelKey: "nav.monitorCenter",
    label: "监控中心",
    items: [
      { labelKey: "nav.monitorExecutions", label: "执行监控", to: "/admin/monitor/executions", icon: Activity },
      { labelKey: "nav.traces", label: "链路追踪", to: "/admin/traces", icon: LineChart },
      { labelKey: "nav.monitorDashboard", label: "可观测仪表盘", to: "/admin/monitor/dashboard", icon: Gauge },
      { labelKey: "nav.auditLogs", label: "审计日志", to: "/admin/audit-logs", icon: ScrollText, roles: ["admin"] },
    ],
  },
  {
    labelKey: "nav.configCenter",
    label: "配置中心",
    items: [
      { labelKey: "nav.models", label: "模型管理", to: "/admin/models", icon: Cpu, roles: ["admin"] },
      { labelKey: "nav.mcpConnectors", label: "MCP & 连接器", to: "/admin/mcp-connectors", icon: Plug, roles: ["admin"] },
      { labelKey: "nav.triggers", label: "调度管理", to: "/admin/triggers", icon: Zap, roles: ["admin"] },
      { labelKey: "nav.users", label: "用户管理", to: "/admin/users", icon: Users, roles: ["admin"] },
      { labelKey: "nav.departments", label: "部门管理", to: "/admin/departments", icon: Building2, roles: ["admin"] },
      { labelKey: "nav.roles", label: "角色权限", to: "/admin/roles", icon: Shield, roles: ["admin"] },
      { labelKey: "nav.sso", label: "SSO 配置", to: "/admin/sso", icon: KeyRound, roles: ["admin"] },
      { labelKey: "nav.memory", label: "记忆管理", to: "/admin/memory", icon: Brain, roles: ["admin"] },
      { labelKey: "nav.safetyCases", label: "安全评估", to: "/admin/safety-cases", icon: FlaskConical, roles: ["admin"] },
      { labelKey: "nav.reportTemplates", label: "报告模板", to: "/admin/report-templates", icon: FileText, roles: ["admin"] },
      { labelKey: "nav.plugins", label: "插件配置", to: "/admin/plugins", icon: Puzzle, roles: ["admin"] },
      { labelKey: "nav.skills", label: "Skill 管理", to: "/admin/skills", icon: Sparkles, roles: ["admin"] },
    ],
    roles: ["admin"],
  },
  {
    labelKey: "nav.knowledgeCenter",
    label: "知识中心",
    items: [
      { labelKey: "nav.documents", label: "文档管理", to: "/admin/documents", icon: FileText, roles: ["admin"] },
      { labelKey: "nav.ontology", label: "本体管理", to: "/admin/ontology", icon: Network, roles: ["admin"] },
      { labelKey: "nav.contributions", label: "贡献审核", to: "/admin/contributions", icon: FileText, roles: ["admin"] },
    ],
    roles: ["admin"],
  },
  {
    labelKey: "nav.evolution",
    label: "演进",
    items: [
      { labelKey: "nav.promotions", label: "技能晋升", to: "/admin/promotions", icon: GitBranch, roles: ["admin"] },
    ],
    roles: ["admin"],
  },
  {
    labelKey: "nav.system",
    label: "系统",
    items: [
      { labelKey: "nav.tenants", label: "多租户管理", to: "/admin/tenants", icon: Globe2, roles: ["super_admin"] },
      { labelKey: "nav.dataManagement", label: "数据管理", to: "/admin/data-management", icon: PackageOpen, roles: ["admin"] },
      { labelKey: "nav.settings", label: "设置", to: "/admin/settings", icon: Settings },
    ],
  },
];

/** Filter nav groups and items by the user's role. super_admin sees admin items too. */
export function filterNavByRole(groups: NavGroup[], role: string | undefined): NavGroup[] {
  if (!role) return [];
  const effectiveRole = role === "super_admin" ? "admin" : role;
  const isSuperAdmin = role === "super_admin";
  return groups
    .filter((g) => !g.roles || g.roles.includes(effectiveRole))
    .map((g) => ({
      ...g,
      items: g.items.filter((item) => {
        if (!item.roles) return true;
        if (isSuperAdmin && item.roles.includes("super_admin")) return true;
        return item.roles.includes(effectiveRole);
      }),
    }))
    .filter((g) => g.items.length > 0);
}
