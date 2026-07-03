import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type StatusBadgeVariant = "default" | "secondary" | "destructive" | "outline" | "success" | "warning";

const variantClasses: Record<StatusBadgeVariant, string> = {
  default: "",
  secondary: "",
  destructive: "",
  outline: "",
  success: "border-transparent bg-success/15 text-success hover:bg-success/15",
  warning: "border-transparent bg-warning/15 text-warning-foreground hover:bg-warning/15",
};

type StatusBadgeProps = {
  children: ReactNode;
  variant?: StatusBadgeVariant;
  className?: string;
};

export function StatusBadge({ children, variant = "secondary", className }: StatusBadgeProps) {
  const semantic = variant === "success" || variant === "warning";

  if (semantic) {
    return (
      <span
        className={cn(
          "inline-flex items-center rounded-full border px-2.5 py-0.5 text-2xs font-medium",
          variantClasses[variant],
          className,
        )}
      >
        {children}
      </span>
    );
  }

  return (
    <Badge variant={variant} className={cn("text-2xs", className)}>
      {children}
    </Badge>
  );
}
