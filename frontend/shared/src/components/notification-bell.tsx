import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Bell } from "lucide-react";
import { apiClient } from "../api/client";
import { cn } from "../lib/utils";

interface NotificationBellProps {
  to: string;
  className?: string;
}

/**
 * P1-T5 — Notification bell with unread count badge.
 * Polls /notifications/unread-count every 30s.
 */
export function NotificationBell({ to, className }: NotificationBellProps) {
  const { data } = useQuery({
    queryKey: ["notifications", "unread-count"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/notifications/unread-count", {});
      if (error || !data) return 0;
      return (data as { unread_count: number }).unread_count;
    },
    refetchInterval: 30_000,
  });

  const count = data ?? 0;

  return (
    <Link
      to={to}
      className={cn(
        "relative rounded-md p-2 text-secondary transition-colors hover:bg-subtle hover:text-foreground",
        className,
      )}
      aria-label={count > 0 ? `通知 (${count} 条未读)` : "通知"}
    >
      <Bell className="h-5 w-5" strokeWidth={1.75} />
      {count > 0 && (
        <span
          className={cn(
            "absolute -right-0.5 -top-0.5 flex items-center justify-center rounded-full bg-danger text-[10px] font-bold text-white",
            count > 99 ? "h-4 min-w-4 px-1" : "h-4 w-4",
          )}
        >
          {count > 99 ? "99+" : count}
        </span>
      )}
    </Link>
  );
}
