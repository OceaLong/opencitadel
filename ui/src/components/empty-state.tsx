import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

type EmptyStateProps = {
  icon?: LucideIcon;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  variant?: "plain" | "dashed";
  className?: string;
};

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  variant = "plain",
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center px-4 py-12 text-center",
        variant === "dashed" && "border-border/70 bg-muted/20 rounded-2xl border border-dashed",
        className,
      )}
    >
      {Icon ? <Icon className="text-muted-foreground/50 mb-3 size-9" /> : null}
      <p className="text-foreground text-sm font-medium">{title}</p>
      {description ? <p className="text-muted-foreground mt-1 text-xs">{description}</p> : null}
      {action ? <div className="mt-4">{action}</div> : null}
    </div>
  );
}
