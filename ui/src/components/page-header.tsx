import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

type PageHeaderProps = {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  size?: "sm" | "md" | "lg";
  className?: string;
  bordered?: boolean;
};

const titleSizes = {
  sm: "text-base font-semibold tracking-tight",
  md: "text-xl font-semibold tracking-tight",
  lg: "text-2xl font-semibold tracking-tight",
};

export function PageHeader({
  title,
  description,
  actions,
  size = "lg",
  className,
  bordered = true,
}: PageHeaderProps) {
  return (
    <div
      className={cn(
        "flex items-start justify-between gap-4",
        bordered && "border-border/70 border-b px-6 py-4",
        !bordered && "px-0 py-0",
        className,
      )}
    >
      <div>
        <h1 className={titleSizes[size]}>{title}</h1>
        {description ? (
          <p className="text-muted-foreground mt-1 text-sm">{description}</p>
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}
