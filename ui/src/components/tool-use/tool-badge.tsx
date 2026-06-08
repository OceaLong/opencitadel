"use client";

import type { LucideIcon } from "lucide-react";

export type ToolBadgeProps = {
  icon: LucideIcon;
  label: string;
  onClick?: () => void;
};

export function ToolBadge({ icon: Icon, label, onClick }: ToolBadgeProps) {
  return (
    <div
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      onKeyDown={
        onClick
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onClick();
              }
            }
          : undefined
      }
      className="border-border/70 bg-muted/60 text-foreground hover:bg-muted inline-flex w-fit max-w-full min-w-0 cursor-pointer items-center gap-1.5 rounded-lg border px-2.5 py-1 text-sm transition-colors"
    >
      <span className="text-muted-foreground flex shrink-0 items-center justify-center">
        <Icon size={16} className="shrink-0" />
      </span>
      <span className="max-w-[480px] truncate">{label}</span>
    </div>
  );
}
