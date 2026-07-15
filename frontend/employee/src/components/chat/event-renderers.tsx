import { useState, type ReactNode } from "react";
import {
  Brain,
  Footprints,
  Hammer,
  ClipboardList,
  CheckCircle2,
  AlertCircle,
  Lightbulb,
  ChevronRight,
  ChevronDown,
} from "lucide-react";
import { cn, type AgentEvent } from "@eaos/shared";
import { ToolResultRenderer } from "./tool-result-renderer";

/**
 * Dispatch a single AgentEvent to its renderer.
 * `token` and `final` are handled by the message bubble (accumulated into
 * message.content) so they return null here.
 */
export function EventRenderer({ event }: { event: AgentEvent }) {
  switch (event.type) {
    case "step":
      return <StepRow event={event} />;
    case "plan":
      return <CollapsibleRow
        icon={<ClipboardList className="h-3 w-3" />}
        label="计划"
        content={event.content}
        tone="default"
      />;
    case "reason":
      return <CollapsibleRow
        icon={<Brain className="h-3 w-3" />}
        label="推理"
        content={event.content}
        tone="info"
      />;
    case "tool_call":
      return (
        <div className="rounded-md border border-border-subtle bg-elevated">
          <div className="flex items-center gap-2 px-3 py-1.5">
            <span className="flex h-5 w-5 items-center justify-center rounded-full bg-warning-subtle text-warning">
              <Hammer className="h-3 w-3" />
            </span>
            <span className="flex-1 truncate text-xs font-medium text-secondary">
              {event.content ? `工具调用 · ${event.content}` : "工具调用"}
            </span>
          </div>
          <div className="border-t border-border-subtle px-3 py-2">
            <ToolResultRenderer event={event} />
          </div>
        </div>
      );
    case "tool_result":
      return <CollapsibleRow
        icon={<CheckCircle2 className="h-3 w-3" />}
        label="工具结果"
        content={event.content}
        tone="success"
      />;
    case "reflect":
      return <CollapsibleRow
        icon={<Lightbulb className="h-3 w-3" />}
        label="反思"
        content={event.content}
        tone="default"
      />;
    case "error":
      return <CollapsibleRow
        icon={<AlertCircle className="h-3 w-3" />}
        label="错误"
        content={event.content}
        tone="danger"
      />;
    case "token":
    case "final":
    default:
      return null;
  }
}

function StepRow({ event }: { event: AgentEvent }) {
  return (
    <div className="flex items-center gap-2 text-xs font-semibold text-foreground">
      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-info-subtle text-info">
        <Footprints className="h-3 w-3" />
      </span>
      <span className="truncate">{event.content ?? "执行步骤"}</span>
    </div>
  );
}

type Tone = "default" | "info" | "success" | "warning" | "danger";

const TONE_CLASS: Record<Tone, string> = {
  default: "bg-subtle text-secondary",
  info: "bg-info-subtle text-info",
  success: "bg-success-subtle text-success",
  warning: "bg-warning-subtle text-warning",
  danger: "bg-danger-subtle text-danger",
};

function CollapsibleRow({
  icon,
  label,
  content,
  tone = "default",
}: {
  icon: ReactNode;
  label: string;
  content?: string | null;
  tone?: Tone;
}) {
  const [open, setOpen] = useState(false);
  const hasContent = !!content?.trim();
  return (
    <div className="rounded-md border border-border-subtle bg-elevated">
      <button
        type="button"
        disabled={!hasContent}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left disabled:cursor-default"
      >
        <span
          className={cn(
            "flex h-5 w-5 items-center justify-center rounded-full",
            TONE_CLASS[tone]
          )}
        >
          {icon}
        </span>
        <span className="flex-1 truncate text-xs font-medium text-secondary">
          {label}
        </span>
        {hasContent &&
          (open ? (
            <ChevronDown className="h-3 w-3 text-tertiary" />
          ) : (
            <ChevronRight className="h-3 w-3 text-tertiary" />
          ))}
      </button>
      {open && hasContent && (
        <pre className="border-t border-border-subtle px-3 py-2 font-mono text-xs whitespace-pre-wrap text-foreground">
          {content}
        </pre>
      )}
    </div>
  );
}
