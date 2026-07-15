import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import { Button, Spinner, cn, toast } from "@eaos/shared";
import { Bell, CheckCheck, CheckCircle2 } from "lucide-react";
import { relativeTime } from "../lib/relative-time";

interface Notification {
  id: string;
  type: string;
  title: string;
  body: string | null;
  read: boolean;
  related_entity_type: string | null;
  related_entity_id: string | null;
  created_at: string;
  read_at: string | null;
}

const TYPE_LABELS: Record<string, string> = {
  approval: "审批",
  trigger: "触发器",
  system: "系统",
};

export default function NotificationsPage() {
  const queryClient = useQueryClient();
  const [unreadOnly, setUnreadOnly] = useState(false);

  const query = useQuery({
    queryKey: ["notifications", { unreadOnly }],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/notifications", {
        params: { query: { unread_only: unreadOnly } },
      });
      if (error || !data) return [] as Notification[];
      return data as unknown as Notification[];
    },
    refetchInterval: 30_000,
  });

  const markReadMutation = useMutation({
    mutationFn: async (id: string) => {
      await apiClient.PUT("/notifications/{notification_id}/read", {
        params: { path: { notification_id: id } },
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["notifications"] });
    },
  });

  const markAllReadMutation = useMutation({
    mutationFn: async () => {
      await apiClient.PUT("/notifications/read-all", {});
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["notifications"] });
      toast.show({ title: "全部标记已读", variant: "success" });
    },
  });

  const notifications = query.data ?? [];
  const unreadCount = notifications.filter((n) => !n.read).length;

  const handleClick = (n: Notification) => {
    if (!n.read) {
      markReadMutation.mutate(n.id);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">通知中心</h1>
            <p className="mt-1 text-sm text-secondary">
              {unreadCount > 0 ? `${unreadCount} 条未读` : "全部已读"}
            </p>
          </div>
          {unreadCount > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => markAllReadMutation.mutate()}
              disabled={markAllReadMutation.isPending}
            >
              <CheckCheck className="h-3.5 w-3.5" />
              全部已读
            </Button>
          )}
        </div>
      </div>

      <div className="border-b border-border-subtle px-8 py-3">
        <div className="flex items-center gap-1 rounded-md bg-subtle p-0.5">
          <button
            type="button"
            onClick={() => setUnreadOnly(false)}
            className={cn(
              "rounded-sm px-2.5 py-1 text-xs font-medium transition-colors",
              !unreadOnly
                ? "bg-elevated text-foreground shadow-sm"
                : "text-secondary hover:text-foreground"
            )}
          >
            全部
          </button>
          <button
            type="button"
            onClick={() => setUnreadOnly(true)}
            className={cn(
              "rounded-sm px-2.5 py-1 text-xs font-medium transition-colors",
              unreadOnly
                ? "bg-elevated text-foreground shadow-sm"
                : "text-secondary hover:text-foreground"
            )}
          >
            未读{unreadCount > 0 && ` (${unreadCount})`}
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-8 pb-8">
        {query.isLoading ? (
          <div className="flex h-40 items-center justify-center">
            <Spinner />
          </div>
        ) : notifications.length === 0 ? (
          <div className="flex h-full items-center justify-center p-8">
            <div className="flex flex-col items-center gap-3 text-center">
              <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
                <CheckCircle2 className="h-8 w-8" strokeWidth={1.5} />
              </div>
              <h3 className="text-2xl font-semibold text-foreground">
                {unreadOnly ? "没有未读通知" : "暂无通知"}
              </h3>
              <p className="max-w-sm text-sm text-secondary">
                {unreadOnly
                  ? "所有通知都已查看"
                  : "环境触发与审批通知将出现在这里"}
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            {notifications.map((n) => (
              <button
                key={n.id}
                type="button"
                onClick={() => handleClick(n)}
                className={cn(
                  "flex w-full items-start gap-3 rounded-md border p-4 text-left transition-all",
                  n.read
                    ? "border-border bg-elevated"
                    : "border-accent/30 bg-accent-subtle/50 hover:shadow-sm"
                )}
              >
                <div
                  className={cn(
                    "mt-0.5 shrink-0",
                    n.read ? "text-tertiary" : "text-accent"
                  )}
                >
                  <Bell className="h-5 w-5" strokeWidth={1.75} />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span
                      className={cn(
                        "text-sm",
                        n.read
                          ? "font-normal text-secondary"
                          : "font-medium text-foreground"
                      )}
                    >
                      {n.title}
                    </span>
                    <span className="rounded-full bg-subtle px-1.5 py-0.5 text-xs text-tertiary">
                      {TYPE_LABELS[n.type] ?? n.type}
                    </span>
                    {!n.read && (
                      <span className="h-2 w-2 shrink-0 rounded-full bg-accent" />
                    )}
                  </div>
                  {n.body && (
                    <p className="mt-0.5 line-clamp-2 text-xs text-secondary">
                      {n.body}
                    </p>
                  )}
                  <span className="mt-1 block text-xs text-tertiary">
                    {relativeTime(n.created_at)}
                  </span>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
