import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export type StatusBadgeVariant =
  | "default"
  | "secondary"
  | "destructive"
  | "outline"
  | "success"
  | "warning"
  | "info";

// 琥珀纪律（spec §2.2）：warning 只许文字 + 细边框，禁止背景块
const variantClasses: Record<StatusBadgeVariant, string> = {
  default: "border-transparent bg-primary/15 text-primary",
  secondary: "border-transparent bg-muted text-muted-foreground",
  destructive: "border-transparent bg-destructive/15 text-destructive",
  outline: "border-border text-foreground",
  success: "border-transparent bg-success/15 text-success",
  warning: "border-warning/40 bg-transparent text-warning",
  info: "border-transparent bg-info/15 text-info",
};

type StatusBadgeProps = {
  children: ReactNode;
  variant?: StatusBadgeVariant;
  className?: string;
  title?: string;
  "data-testid"?: string;
};

export function StatusBadge({
  children,
  variant = "secondary",
  className,
  ...rest
}: StatusBadgeProps) {
  return (
    <span
      className={cn(
        "text-2xs inline-flex items-center rounded-full border px-2.5 py-0.5 font-medium whitespace-nowrap",
        variantClasses[variant],
        className,
      )}
      {...rest}
    >
      {children}
    </span>
  );
}
