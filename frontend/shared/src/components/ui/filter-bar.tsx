import { cn } from "../../lib/utils";

export interface FilterOption {
  label: string;
  value: string;
}

export interface FilterBarProps {
  options: FilterOption[];
  value: string;
  onChange: (value: string) => void;
  className?: string;
}

export function FilterBar({
  options,
  value,
  onChange,
  className,
}: FilterBarProps) {
  return (
    <div className={cn("flex items-center gap-1", className)}>
      {options.map((opt) => (
        <button
          key={opt.value}
          onClick={() => onChange(opt.value)}
          className={cn(
            "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
            value === opt.value
              ? "bg-accent text-white"
              : "text-secondary hover:bg-subtle",
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
