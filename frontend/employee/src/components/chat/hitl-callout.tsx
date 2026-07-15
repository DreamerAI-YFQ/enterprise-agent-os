import { useState } from "react";
import { Clock, ShieldCheck, X } from "lucide-react";
import { Button } from "@eaos/shared";

interface HitlCalloutProps {
  approvalId: string;
  onDecide: (decision: "approved" | "rejected", reason?: string) => void;
  disabled?: boolean;
}

/**
 * F1-T8 — Inline HITL approval callout.
 * Shown when an assistant message is awaiting human approval.
 * Backend embeds approval_id in the error event content; the chat hook
 * extracts it via parseApprovalId() and flips message status.
 */
export function HitlCallout({ approvalId, onDecide, disabled }: HitlCalloutProps) {
  const [showReason, setShowReason] = useState(false);
  const [reason, setReason] = useState("");

  return (
    <div className="mt-3 rounded-md border border-warning bg-warning-subtle px-3 py-2.5">
      <div className="flex items-center gap-2 text-xs font-semibold text-warning">
        <Clock className="h-4 w-4" />
        <span>等待人工审批</span>
        <span className="ml-auto font-mono text-[10px] text-secondary">
          {approvalId.slice(0, 8)}
        </span>
      </div>
      <p className="mt-1.5 text-xs text-secondary">
        该操作需要您批准才能继续执行。
      </p>
      {showReason && (
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="拒绝原因（可选）"
          className="mt-2 w-full resize-none rounded border border-border bg-elevated px-2 py-1.5 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-accent/30"
          rows={2}
        />
      )}
      <div className="mt-2.5 flex gap-2">
        <Button
          size="sm"
          disabled={disabled}
          onClick={() => onDecide("approved")}
        >
          <ShieldCheck className="h-3.5 w-3.5" />
          批准
        </Button>
        {!showReason ? (
          <Button
            size="sm"
            variant="outline"
            disabled={disabled}
            onClick={() => setShowReason(true)}
          >
            <X className="h-3.5 w-3.5" />
            拒绝
          </Button>
        ) : (
          <Button
            size="sm"
            variant="danger"
            disabled={disabled}
            onClick={() => onDecide("rejected", reason || undefined)}
          >
            确认拒绝
          </Button>
        )}
      </div>
    </div>
  );
}
