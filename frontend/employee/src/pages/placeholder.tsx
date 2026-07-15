import { EmptyState, type EmptyStateProps } from "@eaos/shared";

interface PlaceholderProps extends Omit<EmptyStateProps, "icon" | "title"> {
  icon: EmptyStateProps["icon"];
  title: string;
}

/** Generic placeholder for pages not yet implemented (F2+). */
export function Placeholder({ icon, title, description }: PlaceholderProps) {
  return (
    <div className="flex h-full items-center justify-center p-8">
      <EmptyState
        icon={icon}
        title={title}
        description={description ?? "该功能将在后续阶段上线"}
      />
    </div>
  );
}
