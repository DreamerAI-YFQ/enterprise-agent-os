import { useCallback, useRef, useState } from "react";
import {
  parseApprovalId,
  streamInvoke,
  streamResume,
  type AgentEvent,
  type AttachmentRef,
} from "@eaos/shared";
import { apiClient } from "@eaos/shared/api";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  events: AgentEvent[];
  status: "streaming" | "complete" | "error" | "awaiting_approval";
  approvalId?: string;
  error?: string;
  attachments?: AttachmentRef[];
}

export interface SessionSummary {
  id: string;
  agent_id: string;
  title: string | null;
  status: string;
  created_at: string;
  last_active_at: string;
}

interface SessionMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  event_type: string | null;
  created_at: string;
}

export interface UseChatReturn {
  messages: ChatMessage[];
  isStreaming: boolean;
  currentSessionId: string | null;
  sendMessage: (text: string, attachments?: AttachmentRef[]) => void;
  resume: (
    messageId: string,
    decision: "approved" | "rejected",
    reason?: string
  ) => void;
  cancel: () => void;
  clear: () => void;
  loadSession: (sessionId: string) => Promise<string | undefined>;
}

let msgCounter = 0;
function genId(): string {
  msgCounter += 1;
  return `m-${Date.now()}-${msgCounter}`;
}

/**
 * Apply a new SSE event to an assistant message.
 * - token: append content to message.content (typewriter)
 * - final: prefer final content if no tokens yet; otherwise avoid duplication
 * - error: detect HITL approval_id or mark as error
 * - others: kept in events[] for the timeline renderer
 */
function applyEvent(message: ChatMessage, event: AgentEvent): ChatMessage {
  const next: ChatMessage = {
    ...message,
    events: [...message.events, event],
  };
  switch (event.type) {
    case "token":
      if (event.content) next.content += event.content;
      break;
    case "final":
      // `final` carries the authoritative complete response from the
      // backend — replace accumulated tokens to avoid duplication.
      if (event.content) {
        next.content = event.content;
      }
      break;
    case "error": {
      const approvalId = event.content
        ? parseApprovalId(event.content)
        : null;
      if (approvalId) {
        next.status = "awaiting_approval";
        next.approvalId = approvalId;
      } else {
        next.status = "error";
        next.error = event.content ?? "未知错误";
      }
      break;
    }
    default:
      // step / plan / tool_call / tool_result / reflect / reason
      // — kept in events[] for the timeline renderer.
      break;
  }
  return next;
}

export function useChat(agentId: string): UseChatReturn {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const agentIdRef = useRef<string>(agentId);
  agentIdRef.current = agentId;

  // NOTE: No auto-reset on agentId change — the chat page calls clear()
  // explicitly when the user switches agents via the selector. This avoids
  // a race where loadSession() sets agentId and the auto-reset would wipe
  // the just-loaded history.

  const updateMessage = useCallback(
    (id: string, updater: (m: ChatMessage) => ChatMessage) => {
      setMessages((prev) => prev.map((m) => (m.id === id ? updater(m) : m)));
    },
    []
  );

  const sendMessage = useCallback(
    (text: string, attachments?: AttachmentRef[]) => {
      if (!agentIdRef.current) return;
      controllerRef.current?.abort();

      const userMsg: ChatMessage = {
        id: genId(),
        role: "user",
        content: text,
        events: [],
        status: "complete",
        attachments,
      };
      const assistantMsg: ChatMessage = {
        id: genId(),
        role: "assistant",
        content: "",
        events: [],
        status: "streaming",
      };
      setMessages((prev) => [...prev, userMsg, assistantMsg]);

      const controller = streamInvoke(
        {
          agent_id: agentIdRef.current,
          message: text,
          session_id: sessionIdRef.current ?? undefined,
          attachments: attachments && attachments.length > 0 ? attachments : undefined,
        },
        {
          onSessionId: (sid) => {
            sessionIdRef.current = sid;
            setCurrentSessionId(sid);
          },
          onEvent: (event) => {
            updateMessage(assistantMsg.id, (m) => applyEvent(m, event));
          },
          onError: (err) => {
            updateMessage(assistantMsg.id, (m) => ({
              ...m,
              status: "error",
              error: err.message,
            }));
          },
          onDone: () => {
            if (controllerRef.current === controller) {
              controllerRef.current = null;
            }
            updateMessage(assistantMsg.id, (m) =>
              m.status === "streaming" ? { ...m, status: "complete" } : m
            );
          },
        }
      );
      controllerRef.current = controller;
    },
    [updateMessage]
  );

  const resume = useCallback(
    (
      messageId: string,
      decision: "approved" | "rejected",
      reason?: string
    ) => {
      const target = messages.find((m) => m.id === messageId);
      if (!target?.approvalId || !sessionIdRef.current) return;

      updateMessage(messageId, (m) => ({
        ...m,
        status: "streaming",
        error: undefined,
      }));

      const controller = streamResume(
        sessionIdRef.current,
        {
          agent_id: agentIdRef.current,
          approval_id: target.approvalId,
          decision,
          reason: reason ?? null,
        },
        {
          onEvent: (event) => {
            updateMessage(messageId, (m) => applyEvent(m, event));
          },
          onError: (err) => {
            updateMessage(messageId, (m) => ({
              ...m,
              status: "error",
              error: err.message,
            }));
          },
          onDone: () => {
            if (controllerRef.current === controller) {
              controllerRef.current = null;
            }
            updateMessage(messageId, (m) =>
              m.status === "streaming" ? { ...m, status: "complete" } : m
            );
          },
        }
      );
      controllerRef.current = controller;
    },
    [messages, updateMessage]
  );

  const cancel = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    setMessages((prev) =>
      prev.map((m) =>
        m.status === "streaming" ? { ...m, status: "complete" } : m
      )
    );
  }, []);

  const clear = useCallback(() => {
    controllerRef.current?.abort();
    controllerRef.current = null;
    sessionIdRef.current = null;
    setCurrentSessionId(null);
    setMessages([]);
  }, []);

  const loadSession = useCallback(async (
    sessionId: string
  ): Promise<string | undefined> => {
    controllerRef.current?.abort();
    controllerRef.current = null;

    // Fetch session metadata (for agent_id) + messages in parallel.
    const [sessionRes, messagesRes] = await Promise.all([
      apiClient.GET("/sessions/{session_id}", {
        params: { path: { session_id: sessionId } },
      }),
      apiClient.GET("/sessions/{session_id}/messages", {
        params: { path: { session_id: sessionId } },
      }),
    ]);

    const session = sessionRes.data as unknown as SessionSummary | undefined;
    const history = (messagesRes.data as unknown as SessionMessage[] | undefined) ?? [];

    sessionIdRef.current = sessionId;
    setCurrentSessionId(sessionId);

    setMessages(
      history.map((m) => ({
        id: m.id,
        role: m.role,
        content: m.content ?? "",
        events: [],
        status: "complete" as const,
      }))
    );

    return session?.agent_id;
  }, []);

  const isStreaming = messages.some((m) => m.status === "streaming");

  return {
    messages,
    isStreaming,
    currentSessionId,
    sendMessage,
    resume,
    cancel,
    clear,
    loadSession,
  };
}
