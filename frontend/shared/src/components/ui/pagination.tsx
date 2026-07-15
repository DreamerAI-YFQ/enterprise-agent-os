import { ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "../../lib/utils";

export interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  className?: string;
}

export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
  className,
}: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const start = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const end = Math.min(page * pageSize, total);

  return (
    <div
      className={cn(
        "flex items-center justify-between px-1 py-3 text-xs text-tertiary",
        className,
      )}
    >
      <span>
        {total > 0
          ? `共 ${total} 条，第 ${start}-${end} 条`
          : "暂无数据"}
      </span>
      <div className="flex items-center gap-1">
        <button
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
          className={cn(
            "rounded-md p-1.5 transition-colors",
            page <= 1
              ? "cursor-not-allowed text-tertiary/40"
              : "text-secondary hover:bg-subtle hover:text-foreground",
          )}
        >
          <ChevronLeft className="h-3.5 w-3.5" />
        </button>
        <span className="min-w-[60px] text-center">
          {page} / {totalPages}
        </span>
        <button
          disabled={page >= totalPages}
          onClick={() => onPageChange(page + 1)}
          className={cn(
            "rounded-md p-1.5 transition-colors",
            page >= totalPages
              ? "cursor-not-allowed text-tertiary/40"
              : "text-secondary hover:bg-subtle hover:text-foreground",
          )}
        >
          <ChevronRight className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}
