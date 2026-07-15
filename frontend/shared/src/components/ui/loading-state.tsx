import { cn } from "../../lib/utils";
import { Spinner } from "./spinner";

export interface LoadingStateProps {
  className?: string;
  label?: string;
}

export function LoadingState({ className, label }: LoadingStateProps) {
  return (
    <div
      className={cn(
        "flex h-40 flex-col items-center justify-center gap-2 text-tertiary",
        className,
      )}
    >
      <Spinner />
      {label && <span className="text-xs">{label}</span>}
    </div>
  );
}
