import { useAuthStore } from "../api/auth-store";
import { resolveBackendUrl } from "../api/backend-url";
import type { AgentEvent, InvokeRequest, ResumeRequest } from "../types/agent-event";

interface StreamCallbacks {
  onEvent: (event: AgentEvent) => void;
  onSessionId?: (sessionId: string) => void;
  onError?: (error: Error) => void;
  onDone?: () => void;
}

const SSE_DONE = "[DONE]";

/**
 * Parse an SSE chunk buffer into complete events.
 * SSE format: `data: {json}\n\n` per event.
 * Returns [parsed events, remaining buffer].
 */
function parseSseBuffer(
  buffer: string
): [AgentEvent[], string] {
  const events: AgentEvent[] = [];
  const parts = buffer.split("\n\n");
  // Last part is incomplete (no trailing \n\n) — keep it as remainder
  const remainder = parts.pop() ?? "";

  for (const part of parts) {
    const trimmed = part.trim();
    if (!trimmed) continue;
    // Each part may have multiple `data:` lines; join them
    const dataLines = trimmed
      .split("\n")
      .filter((l) => l.startsWith("data:"))
      .map((l) => l.slice(5).trim());
    if (dataLines.length === 0) continue;
    const raw = dataLines.join("");
    if (raw === SSE_DONE) continue; // sentinel handled by caller
    try {
      events.push(JSON.parse(raw) as AgentEvent);
    } catch {
      // Skip malformed events
    }
  }
  return [events, remainder];
}

async function consumeStream(
  response: Response,
  callbacks: StreamCallbacks,
  signal: AbortSignal
): Promise<void> {
  const sessionId = response.headers.get("x-session-id");
  if (sessionId && callbacks.onSessionId) {
    callbacks.onSessionId(sessionId);
  }

  const reader = response.body?.getReader();
  if (!reader) {
    callbacks.onError?.(new Error("No response body"));
    return;
  }

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      if (signal.aborted) break;
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const [events, remainder] = parseSseBuffer(buffer);
      buffer = remainder;
      for (const event of events) {
        callbacks.onEvent(event);
      }
    }
    // Flush any remaining data
    if (buffer.trim()) {
      const [events] = parseSseBuffer(buffer + "\n\n");
      for (const event of events) {
        callbacks.onEvent(event);
      }
    }
    callbacks.onDone?.();
  } catch (err) {
    if (signal.aborted) return;
    callbacks.onError?.(err instanceof Error ? err : new Error(String(err)));
  } finally {
    reader.releaseLock();
  }
}

function buildHeaders(token: string | null): HeadersInit {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

/**
 * POST /invoke — stream an agent conversation turn.
 * Returns an AbortController to cancel the stream.
 */
export function streamInvoke(
  body: InvokeRequest,
  callbacks: StreamCallbacks
): AbortController {
  const controller = new AbortController();
  const token = useAuthStore.getState().token;

  fetch(resolveBackendUrl("/invoke"), {
    method: "POST",
    headers: buildHeaders(token),
    body: JSON.stringify(body),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const text = await response.text().catch(() => "");
        throw new Error(text || `HTTP ${response.status}`);
      }
      return consumeStream(response, callbacks, controller.signal);
    })
    .catch((err) => {
      if (!controller.signal.aborted) {
        callbacks.onError?.(
          err instanceof Error ? err : new Error(String(err))
        );
      }
    });

  return controller;
}

/**
 * POST /interrupt/{sessionId}/resume — continue a paused agent after HITL.
 * Returns an AbortController to cancel the stream.
 */
export function streamResume(
  sessionId: string,
  body: ResumeRequest,
  callbacks: StreamCallbacks
): AbortController {
  const controller = new AbortController();
  const token = useAuthStore.getState().token;

  fetch(resolveBackendUrl(`/interrupt/${sessionId}/resume`), {
    method: "POST",
    headers: buildHeaders(token),
    body: JSON.stringify(body),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const text = await response.text().catch(() => "");
        throw new Error(text || `HTTP ${response.status}`);
      }
      return consumeStream(response, callbacks, controller.signal);
    })
    .catch((err) => {
      if (!controller.signal.aborted) {
        callbacks.onError?.(
          err instanceof Error ? err : new Error(String(err))
        );
      }
    });

  return controller;
}
