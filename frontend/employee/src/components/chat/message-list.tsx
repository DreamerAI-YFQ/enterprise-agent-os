import { useEffect, useRef } from "react";
import { EmptyState } from "@eaos/shared";
import { MessageSquare } from "lucide-react";
import type { ChatMessage } from "../../hooks/use-chat";
import { MessageBubble } from "./message-bubble";

interface MessageListProps {
  messages: ChatMessage[];
  onApprove?: (
    messageId: string,
    decision: "approved" | "rejected",
    reason?: string
  ) => void;
}

export function MessageList({ messages, onApprove }: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="flex h-full items-center justify-center px-6">
        <EmptyState
          icon={MessageSquare}
          title="开始与 AI 助手对话"
          description="在下方输入框中提问，助手将根据您的指令规划步骤、调用工具并返回结果。"
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6 px-6 py-8">
      {messages.map((m) => (
        <MessageBubble key={m.id} message={m} onApprove={onApprove} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
