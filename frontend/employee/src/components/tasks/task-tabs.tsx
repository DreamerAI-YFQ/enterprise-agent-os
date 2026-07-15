import { cn } from "@eaos/shared";

export type TaskTab = "pending" | "running" | "completed";

interface TaskTabsProps {
  active: TaskTab;
  counts: Record<TaskTab, number | undefined>;
  onChange: (tab: TaskTab) => void;
}

const TABS: { value: TaskTab; label: string }[] = [
  { value: "pending", label: "待办" },
  { value: "running", label: "进行中" },
  { value: "completed", label: "已完成" },
];

export function TaskTabs({ active, counts, onChange }: TaskTabsProps) {
  return (
    <div className="flex items-center gap-1">
      {TABS.map((tab) => {
        const count = counts[tab.value];
        const isActive = active === tab.value;
        return (
          <button
            key={tab.value}
            type="button"
            onClick={() => onChange(tab.value)}
            className={cn(
              "flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-medium transition-colors duration-fast ease-out",
              isActive
                ? "bg-accent-subtle text-accent"
                : "text-secondary hover:bg-subtle hover:text-foreground"
            )}
          >
            {tab.label}
            {count !== undefined && count > 0 && (
              <span
                className={cn(
                  "rounded-full px-1.5 py-0.5 text-xs font-semibold",
                  isActive
                    ? "bg-accent text-white"
                    : "bg-subtle text-tertiary"
                )}
              >
                {count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
