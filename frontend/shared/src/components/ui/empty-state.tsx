import * as React from "react";
import type { LucideIcon } from "lucide-react";
import { cn } from "../../lib/utils";

export interface EmptyStateProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Lucide icon component rendered in a soft circle above the title */
  icon?: LucideIcon;
  /** Headline — keep under 8 words; uses text-2xl / 600 */
  title: string;
  /** Supporting copy — one sentence guiding the next action */
  description?: string;
  /** Primary action button(s) — pass a single Button or a fragment of actions */
  action?: React.ReactNode;
  /** Compact variant for inline panels (smaller icon + spacing) */
  compact?: boolean;
}

/**
 * Unified empty state used across admin & employee surfaces.
 * Follows the Apple-style spec: icon circle + bold headline + soft
 * supporting line + a single accent action. Never leave a screen blank.
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  compact = false,
  className,
  ...props
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center text-center",
        compact ? "gap-2 py-8" : "gap-3 py-16",
        className
      )}
      {...props}
    >
      {Icon && (
        <div
          className={cn(
            "flex items-center justify-center rounded-full bg-subtle text-tertiary",
            compact ? "h-10 w-10" : "h-16 w-16"
          )}
        >
          <Icon className={compact ? "h-5 w-5" : "h-8 w-8"} strokeWidth={1.5} />
        </div>
      )}
      <h3
        className={cn(
          "font-semibold text-foreground",
          compact ? "text-base" : "text-2xl"
        )}
      >
        {title}
      </h3>
      {description && (
        <p
          className={cn(
            "max-w-sm text-secondary",
            compact ? "text-sm" : "text-base"
          )}
        >
          {description}
        </p>
      )}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
