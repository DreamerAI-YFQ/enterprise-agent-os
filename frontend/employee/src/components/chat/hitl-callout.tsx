import { Clock, RefreshCw } from "lucide-react";
import { Button } from "@eaos/shared";

interface HitlCalloutProps {
  approvalId: string;
  onResume: () => void;
  disabled?: boolean;
  error?: string;
}

/** Requester-side HITL status card; approval decisions belong to the admin UI. */
export function HitlCallout({
  approvalId,
  onResume,
  disabled,
  error,
}: HitlCalloutProps) {
  return (
    <div className="mt-3 rounded-md border border-warning bg-warning-subtle px-3 py-2.5">
      <div className="flex items-center gap-2 text-xs font-semibold text-warning">
        <Clock className="h-4 w-4" />
        <span>等待管理员审批</span>
        <span className="ml-auto font-mono text-[10px] text-secondary">
          {approvalId.slice(0, 8)}
        </span>
      </div>
      <p className="mt-1.5 text-xs text-secondary">
        请求已安全暂停。请由管理员在审批中心独立审核；审批通过后再继续执行。
      </p>
      {error && <p className="mt-1.5 text-xs text-danger">{error}</p>}
      <div className="mt-2.5 flex gap-2">
        <Button size="sm" disabled={disabled} onClick={onResume}>
          <RefreshCw className="h-3.5 w-3.5" />
          审批完成，继续执行
        </Button>
      </div>
    </div>
  );
}
