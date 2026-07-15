import { cn } from "@eaos/shared";

export type TaskTypeFilter = "all" | "approval" | "session";
export type TaskSortBy = "updated" | "created";

interface TaskFiltersProps {
  typeFilter: TaskTypeFilter;
  sortBy: TaskSortBy;
  onTypeChange: (v: TaskTypeFilter) => void;
  onSortChange: (v: TaskSortBy) => void;
}

const TYPE_OPTIONS: { value: TaskTypeFilter; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "approval", label: "审批" },
  { value: "session", label: "会话" },
];

const SORT_OPTIONS: { value: TaskSortBy; label: string }[] = [
  { value: "updated", label: "最近更新" },
  { value: "created", label: "最近创建" },
];

export function TaskFilters({
  typeFilter,
  sortBy,
  onTypeChange,
  onSortChange,
}: TaskFiltersProps) {
  return (
    <div className="flex items-center gap-3">
      <div className="flex items-center gap-1 rounded-md bg-subtle p-0.5">
        {TYPE_OPTIONS.map((opt) => (
          <button
            key={opt.value}
            type="button"
            onClick={() => onTypeChange(opt.value)}
            className={cn(
              "rounded-sm px-2.5 py-1 text-xs font-medium transition-colors duration-fast ease-out",
              typeFilter === opt.value
                ? "bg-elevated text-foreground shadow-sm"
                : "text-secondary hover:text-foreground"
            )}
          >
            {opt.label}
          </button>
        ))}
      </div>
      <select
        value={sortBy}
        onChange={(e) => onSortChange(e.target.value as TaskSortBy)}
        className="rounded-md border border-border bg-elevated px-2 py-1 text-xs text-secondary transition-colors hover:border-border-strong focus:border-accent focus:outline-none"
      >
        {SORT_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    </div>
  );
}
