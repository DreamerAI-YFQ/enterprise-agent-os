import {
  MessageSquare,
  ListTodo,
  Bell,
  BookOpen,
  BarChart3,
  Settings,
  Brain,
  Sparkles,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  /** i18n key under the `nav.*` namespace, e.g. "nav.chat". */
  labelKey: string;
  /** Fallback label used when i18n is not yet initialised. */
  label: string;
  to: string;
  icon: LucideIcon;
}

export const navItems: NavItem[] = [
  { labelKey: "nav.chat", label: "对话", to: "/app", icon: MessageSquare },
  { labelKey: "nav.tasks", label: "任务", to: "/app/tasks", icon: ListTodo },
  { labelKey: "nav.notifications", label: "通知", to: "/app/notifications", icon: Bell },
  { labelKey: "nav.skills", label: "技能", to: "/app/skills", icon: Sparkles },
  { labelKey: "nav.memory", label: "记忆", to: "/app/memory", icon: Brain },
  { labelKey: "nav.knowledge", label: "知识", to: "/app/knowledge", icon: BookOpen },
  { labelKey: "nav.data", label: "数据", to: "/app/bi", icon: BarChart3 },
  { labelKey: "nav.settings", label: "设置", to: "/app/settings", icon: Settings },
];
