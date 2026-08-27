import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

const toneClasses = {
  approval:
    "border-approval-subtle bg-approval-subtle border-l-accent-approval animate-approval-pulse",
  info: "border-info-subtle bg-info-subtle border-l-accent-info",
} as const;

type ApprovalBarProps = {
  tone?: keyof typeof toneClasses;
  className?: string;
  children: ReactNode;
};

export function ApprovalBar({ tone = "approval", className, children }: ApprovalBarProps) {
  return (
    <div
      className={cn(
        "shadow-card rounded-lg border border-l-4 px-4 py-3",
        toneClasses[tone],
        className,
      )}
    >
      {children}
    </div>
  );
}
