import {
  CheckCircle2,
  Clock,
  History,
  type LucideIcon,
} from "lucide-react";
import { EmptyState } from "@eaos/shared";

export type TaskTab = "pending" | "running" | "completed";

const EMPTY_STATES: Record<
  TaskTab,
  { icon: LucideIcon; title: string; description: string }
> = {
  pending: {
    icon: CheckCircle2,
    title: "没有待办任务",
    description: "所有审批都处理完了，继续保持",
  },
  running: {
    icon: Clock,
    title: "暂无进行中任务",
    description: "当前没有正在执行的 Agent 会话",
  },
  completed: {
    icon: History,
    title: "暂无已完成任务",
    description: "完成的会话将出现在这里，供你回顾",
  },
};

interface TaskEmptyStateProps {
  tab: TaskTab;
}

export function TaskEmptyState({ tab }: TaskEmptyStateProps) {
  const config = EMPTY_STATES[tab];
  return (
    <div className="flex h-full items-center justify-center p-8">
      <EmptyState
        icon={config.icon}
        title={config.title}
        description={config.description}
      />
    </div>
  );
}
