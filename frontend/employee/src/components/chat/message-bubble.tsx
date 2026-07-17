import { AlertTriangle, FileText, Image as ImageIcon } from "lucide-react";
import { Spinner, resolveBackendUrl } from "@eaos/shared";
import type { AttachmentRef } from "@eaos/shared";
import type { ChatMessage } from "../../hooks/use-chat";
import { MessageTimeline } from "./message-timeline";
import { HitlCallout } from "./hitl-callout";

interface MessageBubbleProps {
  message: ChatMessage;
  onResumeApproval?: (messageId: string) => void;
}

/** Render image thumbnails (click to open) and file chips for attachments. */
function AttachmentList({ items }: { items: AttachmentRef[] }) {
  const images = items.filter((a) => a.type === "image");
  const files = items.filter((a) => a.type === "file");

  return (
    <div className="mb-2 space-y-2">
      {images.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {images.map((img) => {
            const src = resolveBackendUrl(img.url);
            return (
            <a
              key={img.file_id}
              href={src}
              target="_blank"
              rel="noopener noreferrer"
              className="block overflow-hidden rounded-md border border-white/30"
            >
              <img
                src={src}
                alt={img.name}
                className="h-20 w-20 object-cover"
                loading="lazy"
              />
            </a>
            );
          })}
        </div>
      )}
      {files.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {files.map((f) => (
            <a
              key={f.file_id}
              href={resolveBackendUrl(f.url)}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 rounded-md bg-white/20 px-2 py-1 text-xs text-white hover:bg-white/30"
            >
              {f.type === "image" ? (
                <ImageIcon className="h-3 w-3" />
              ) : (
                <FileText className="h-3 w-3" />
              )}
              <span className="max-w-[140px] truncate">{f.name}</span>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

export function MessageBubble({ message, onResumeApproval }: MessageBubbleProps) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="flex max-w-[80%] flex-col items-end">
          {message.attachments && message.attachments.length > 0 && (
            <div className="mb-1 w-full">
              <AttachmentList items={message.attachments} />
            </div>
          )}
          <div className="whitespace-pre-wrap rounded-lg rounded-tr-sm bg-accent px-4 py-2.5 text-sm text-white">
            {message.content}
          </div>
        </div>
      </div>
    );
  }

  const isStreaming = message.status === "streaming";
  const showThinking = isStreaming && !message.content;

  return (
    <div className="flex gap-3">
      <div className="w-full max-w-[85%]">
        <div className="rounded-lg rounded-tl-sm border border-border-subtle bg-elevated px-4 py-3 shadow-sm">
          {message.content && (
            <div className="whitespace-pre-wrap text-sm text-foreground">
              {message.content}
              {isStreaming && (
                <span
                  className="ml-0.5 inline-block h-3.5 w-0.5 animate-pulse bg-accent align-middle"
                  aria-hidden
                />
              )}
            </div>
          )}
          {showThinking && (
            <div className="flex items-center gap-2 text-sm text-secondary">
              <Spinner size="sm" />
              <span>正在思考…</span>
            </div>
          )}

          <MessageTimeline events={message.events} />

          {message.status === "error" && (
            <div className="mt-3 flex items-start gap-2 rounded-md bg-danger-subtle px-3 py-2 text-xs text-danger">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>{message.error ?? "执行出错"}</span>
            </div>
          )}

          {message.status === "awaiting_approval" &&
            message.approvalId &&
            onResumeApproval && (
              <HitlCallout
                approvalId={message.approvalId}
                disabled={false}
                error={message.error}
                onResume={() => onResumeApproval(message.id)}
              />
            )}
        </div>
      </div>
    </div>
  );
}
