import type { LucideIcon } from "lucide-react";

interface PlaceholderProps {
  icon: LucideIcon;
  title: string;
  description: string;
}

export function Placeholder({ icon: Icon, title, description }: PlaceholderProps) {
  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border-subtle px-8 py-6">
        <h1 className="text-2xl font-semibold text-foreground">{title}</h1>
        <p className="mt-1 text-sm text-secondary">{description}</p>
      </div>
      <div className="flex flex-1 items-center justify-center p-8">
        <div className="flex flex-col items-center gap-3 text-center">
          <div className="flex h-16 w-16 items-center justify-center rounded-full bg-subtle text-tertiary">
            <Icon className="h-8 w-8" strokeWidth={1.5} />
          </div>
          <h3 className="text-lg font-medium text-foreground">即将上线</h3>
          <p className="max-w-sm text-sm text-secondary">
            此功能正在开发中，敬请期待
          </p>
        </div>
      </div>
    </div>
  );
}
