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
        "flex flex-col items-start gap-3 sm:flex-row sm:items-start sm:justify-between",
        bordered && "border-border/70 border-b px-4 py-4 sm:px-6",
        !bordered && "px-0 py-0",
        className,
      )}
    >
      <div className="min-w-0 flex-1">
        <h1 className={titleSizes[size]}>{title}</h1>
        {description ? (
          <p className="text-muted-foreground mt-1 text-sm">{description}</p>
        ) : null}
      </div>
      {actions ? (
        <div className="flex w-full shrink-0 flex-wrap items-center gap-2 sm:w-auto sm:justify-end">
          {actions}
        </div>
      ) : null}
    </div>
  );
}
