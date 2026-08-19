import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

const toneClasses = {
  gate: "border-gate-subtle bg-gate-subtle border-l-accent-gate animate-gate-pulse",
  info: "border-info-subtle bg-info-subtle border-l-accent-info",
} as const;

type ApprovalBarProps = {
  tone?: keyof typeof toneClasses;
  className?: string;
  children: ReactNode;
};

export function ApprovalBar({ tone = "gate", className, children }: ApprovalBarProps) {
  return (
    <div
      className={cn(
        "rounded-lg border border-l-4 px-4 py-3 shadow-card",
        toneClasses[tone],
        className,
      )}
    >
      {children}
    </div>
  );
}
