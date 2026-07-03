import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

type ApprovalBarTone = "amber" | "blue";

const toneClasses: Record<ApprovalBarTone, string> = {
  amber: "border-warning-subtle bg-warning-subtle",
  blue: "border-info-subtle bg-info-subtle",
};

type ApprovalBarProps = {
  tone?: ApprovalBarTone;
  className?: string;
  children: ReactNode;
};

export function ApprovalBar({ tone = "amber", className, children }: ApprovalBarProps) {
  return (
    <div className={cn("rounded-xl border px-4 py-3 shadow-card", toneClasses[tone], className)}>
      {children}
    </div>
  );
}
