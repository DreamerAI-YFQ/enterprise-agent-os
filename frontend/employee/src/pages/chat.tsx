import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { apiClient } from "@eaos/shared/api";
import { Button, Spinner } from "@eaos/shared";
import { ChevronDown, Eraser } from "lucide-react";
import { SessionSidebar } from "../components/chat/session-sidebar";
import { MessageList } from "../components/chat/message-list";
import { ChatInput } from "../components/chat/chat-input";
import { useChat, type SessionSummary } from "../hooks/use-chat";

interface AgentSummary {
  id: string;
  name: string;
  description?: string | null;
  status?: string;
}

/**
 * F1-T5..T11 — Employee chat page.
 *
 * Layout: [Session Sidebar 260px] [Chat Area flex-1]
 *
 * Wires:
 * - Session list (GET /sessions) with new/select/delete + history loading
 * - Agent selector (GET /agents) — auto-picks first agent
 * - SSE streaming chat (useChat → streamInvoke/streamResume)
 * - AgentEvent renderers + plan-execute-reflect timeline
 * - HITL approval callout
 */
export default function ChatPage() {
  const queryClient = useQueryClient();
  const [agentId, setAgentId] = useState<string>("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);

  // -- Agent list --
  const agentsQuery = useQuery({
    queryKey: ["agents"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/agents", {});
      if (error || !data) return [] as AgentSummary[];
      return data as unknown as AgentSummary[];
    },
  });

  // Auto-pick first agent.
  useEffect(() => {
    if (!agentId && agentsQuery.data && agentsQuery.data.length > 0) {
      setAgentId(agentsQuery.data[0].id);
    }
  }, [agentId, agentsQuery.data]);

  // -- Session list --
  const sessionsQuery = useQuery({
    queryKey: ["sessions"],
    queryFn: async () => {
      const { data, error } = await apiClient.GET("/sessions", {});
      if (error || !data) return [] as SessionSummary[];
      return data as unknown as SessionSummary[];
    },
  });

  // Refresh session list when a new session is created (sessionId goes
  // from null → value after the first message of a new conversation).
  const chat = useChat(agentId);
  useEffect(() => {
    if (chat.currentSessionId) {
      void queryClient.invalidateQueries({ queryKey: ["sessions"] });
    }
  }, [chat.currentSessionId, queryClient]);

  // Close agent picker on outside click.
  useEffect(() => {
    if (!pickerOpen) return;
    const onDocClick = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) {
        setPickerOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [pickerOpen]);

  // -- Delete session --
  const deleteMutation = useMutation({
    mutationFn: async (sessionId: string) => {
      await apiClient.DELETE("/sessions/{session_id}", {
        params: { path: { session_id: sessionId } },
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
  });

  const handleSelectSession = async (sessionId: string) => {
    const sessionAgentId = await chat.loadSession(sessionId);
    if (sessionAgentId) setAgentId(sessionAgentId);
  };

  // Load session from URL query param (e.g. navigating from Tasks page).
  const [searchParams, setSearchParams] = useSearchParams();
  const sessionFromUrl = searchParams.get("session");
  useEffect(() => {
    if (!sessionFromUrl) return;
    // Clear param immediately to prevent double-loading on re-render.
    const next = new URLSearchParams(searchParams);
    next.delete("session");
    setSearchParams(next, { replace: true });
    if (sessionFromUrl === chat.currentSessionId) return;
    void (async () => {
      const agentIdFromSession = await chat.loadSession(sessionFromUrl);
      if (agentIdFromSession) setAgentId(agentIdFromSession);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionFromUrl]);

  const handleDeleteSession = (sessionId: string) => {
    deleteMutation.mutate(sessionId);
    if (sessionId === chat.currentSessionId) {
      chat.clear();
    }
  };

  const handleNewSession = () => {
    chat.clear();
  };

  const handleAgentSelect = (newAgentId: string) => {
    setAgentId(newAgentId);
    chat.clear();
    setPickerOpen(false);
  };

  const currentAgent = agentsQuery.data?.find((a) => a.id === agentId);

  return (
    <div className="flex h-full">
      {/* Session Sidebar */}
      <SessionSidebar
        sessions={sessionsQuery.data ?? []}
        isLoading={sessionsQuery.isLoading}
        currentSessionId={chat.currentSessionId}
        onSelect={(sid) => void handleSelectSession(sid)}
        onNew={handleNewSession}
        onDelete={handleDeleteSession}
      />

      {/* Chat Area */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border-subtle px-6 py-3">
          <div className="relative" ref={pickerRef}>
            <button
              type="button"
              onClick={() => setPickerOpen((v) => !v)}
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm font-medium text-foreground hover:bg-subtle disabled:opacity-50"
              disabled={agentsQuery.isLoading}
            >
              {agentsQuery.isLoading ? (
                <>
                  <Spinner size="sm" />
                  <span className="text-secondary">加载助手…</span>
                </>
              ) : (
                <>
                  <span>{currentAgent?.name ?? "选择助手"}</span>
                  <ChevronDown className="h-4 w-4 text-tertiary" />
                </>
              )}
            </button>
            {pickerOpen && (
              <div className="absolute left-0 top-full z-popover mt-1 w-72 rounded-md border border-border bg-elevated shadow-md">
                {agentsQuery.data?.length === 0 && (
                  <div className="px-3 py-2 text-xs text-secondary">
                    暂无可用助手
                  </div>
                )}
                {agentsQuery.data?.map((agent) => (
                  <button
                    key={agent.id}
                    type="button"
                    onClick={() => handleAgentSelect(agent.id)}
                    className="block w-full px-3 py-2 text-left hover:bg-subtle"
                  >
                    <div className="text-sm font-medium text-foreground">
                      {agent.name}
                    </div>
                    {agent.description && (
                      <div className="mt-0.5 line-clamp-2 text-xs text-secondary">
                        {agent.description}
                      </div>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={chat.clear}
            disabled={chat.messages.length === 0}
          >
            <Eraser className="h-3.5 w-3.5" />
            清空对话
          </Button>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto">
          <MessageList messages={chat.messages} onApprove={chat.resume} />
        </div>

        {/* Input */}
        <ChatInput
          onSend={chat.sendMessage}
          onCancel={chat.cancel}
          isStreaming={chat.isStreaming}
          disabled={!agentId}
        />
      </div>
    </div>
  );
}
