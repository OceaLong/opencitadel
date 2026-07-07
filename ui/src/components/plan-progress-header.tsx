"use client";

import { Progress } from "@/components/ui/progress";

import { cn } from "@/lib/utils";

export type PlanProgressHeaderProps = {
  title: string;
  completedCount: number;
  totalCount: number;
  progressClassName?: string;
  className?: string;
};

export function PlanProgressHeader({
  title,
  completedCount,
  totalCount,
  progressClassName,
  className,
}: PlanProgressHeaderProps) {
  const percent = totalCount > 0 ? (completedCount / totalCount) * 100 : 0;

  return (
    <div className={cn("space-y-2 px-2", className)}>
      <div className="flex items-center justify-between gap-3">
        <span className="text-foreground font-semibold">{title}</span>
        <span className="text-muted-foreground text-xs tabular-nums">
          {completedCount} / {totalCount}
        </span>
      </div>
      <Progress value={percent} className={progressClassName} />
    </div>
  );
}
