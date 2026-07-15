import { AlertTriangle, ArrowRight, MessageSquare } from "lucide-react";
import { cn } from "@eaos/shared";
import { relativeTime } from "../../lib/relative-time";

export interface Task {
  id: string;
  type: "approval" | "session";
  status: "pending" | "running" | "completed";
  title: string;
  description: string | null;
  agent_id: string | null;
  session_id: string | null;
  created_at: string;
  updated_at: string;
  related: {
    approval_id?: string;
    reason?: string;
    requested_by?: string;
    thread_id?: string;
  };
}

interface TaskCardProps {
  task: Task;
  onClick?: (task: Task) => void;
}

export function TaskCard({ task, onClick }: TaskCardProps) {
  const isApproval = task.type === "approval";
  const Icon = isApproval ? AlertTriangle : MessageSquare;

  return (
    <button
      type="button"
      onClick={() => onClick?.(task)}
      disabled={!onClick}
      className={cn(
        "flex w-full items-start gap-3 rounded-md border border-border bg-elevated p-4 text-left shadow-sm transition-all duration-base ease-out",
        onClick &&
          "cursor-pointer hover:border-border-strong hover:shadow-md"
      )}
    >
      <div
        className={cn(
          "mt-0.5 shrink-0",
          isApproval ? "text-warning" : "text-secondary"
        )}
      >
        <Icon className="h-5 w-5" strokeWidth={1.75} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="line-clamp-1 text-sm font-medium text-foreground">
          {task.title}
        </div>
        {task.description && (
          <div className="mt-0.5 line-clamp-2 text-xs text-secondary">
            {task.description}
          </div>
        )}
        <div className="mt-1.5 flex items-center gap-2 text-xs text-tertiary">
          {task.session_id && (
            <span className="font-mono">
              {task.session_id.slice(0, 8)}
            </span>
          )}
          <span>·</span>
          <span>{relativeTime(task.updated_at)}</span>
        </div>
      </div>
      {onClick && (
        <ArrowRight
          className="mt-1 h-4 w-4 shrink-0 text-tertiary"
          strokeWidth={1.75}
        />
      )}
    </button>
  );
}
