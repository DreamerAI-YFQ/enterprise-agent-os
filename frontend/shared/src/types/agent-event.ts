/** AgentEvent — mirrors the Python @dataclass in packages/agent runner.py */

export type AgentEventType =
  | "token"
  | "step"
  | "plan"
  | "tool_call"
  | "tool_result"
  | "reflect"
  | "reason"
  | "approval_required"
  | "final"
  | "error";

export interface AgentEvent {
  type: AgentEventType;
  content: string | null;
  agent_id?: string | null;
  metadata?: Record<string, unknown> | null;
}

/** Reference to an uploaded file (returned by POST /upload). */
export interface AttachmentRef {
  file_id: string;
  url: string; // /uploads/{tenant}/{file_id}.{ext}
  type: "image" | "file";
  name: string;
  mime_type: string;
}

/** POST /invoke request body */
export interface InvokeRequest {
  agent_id: string;
  message: string;
  session_id?: string | null;
  attachments?: AttachmentRef[];
}

/** POST /interrupt/{session_id}/resume request body */
export interface ResumeRequest {
  agent_id: string;
  approval_id: string;
  /** Deprecated compatibility field; the server reads the persisted decision. */
  decision?: "approved" | "rejected" | "";
  reason?: string | null;
}

/**
 * Parse an approval_id from an error event content.
 * The backend embeds it in the message:
 *   "approval {uuid} required for {op} on {resource}"
 */
const APPROVAL_ID_RE = /(?:approval(?:_id)?[=:\s(]+)([0-9a-f-]{36})/i;

export function parseApprovalId(content: string): string | null {
  try {
    const payload = JSON.parse(content) as Record<string, unknown>;
    if (
      typeof payload.approval_id === "string" &&
      /^[0-9a-f-]{36}$/i.test(payload.approval_id)
    ) {
      return payload.approval_id;
    }
  } catch {
    // Older servers embed the id in plain text; fall through to the regex.
  }
  const m = content.match(APPROVAL_ID_RE);
  return m?.[1] ?? null;
}
