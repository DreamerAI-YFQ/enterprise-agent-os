import * as React from "react";
import { Loader2 } from "lucide-react";
import { cn } from "../../lib/utils";

interface SpinnerProps extends React.HTMLAttributes<SVGSVGElement> {
  size?: "sm" | "md" | "lg";
}

const sizeMap = {
  sm: "h-4 w-4",
  md: "h-5 w-5",
  lg: "h-8 w-8",
};

export function Spinner({ size = "md", className, ...props }: SpinnerProps) {
  return (
    <Loader2
      className={cn("animate-spin text-tertiary", sizeMap[size], className)}
      aria-hidden
      {...props}
    />
  );
}
