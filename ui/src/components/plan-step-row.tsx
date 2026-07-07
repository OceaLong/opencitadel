"use client";

import { PlanStepStatusIcon } from "@/components/plan-step-status-icon";

import type { ExecutionStatus } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export type PlanStepRowProps = {
  description: string;
  status: ExecutionStatus;
  /** 1-based index for pending steps in approval view */
  index?: number;
  highlight?: boolean;
  variant?: "default" | "timeline";
  isLast?: boolean;
  className?: string;
};

export function PlanStepRow({
  description,
  status,
  index,
  highlight = false,
  variant = "default",
  isLast = false,
  className,
}: PlanStepRowProps) {
  const isCompleted = status === "completed";

  return (
    <div
      className={cn(
        "flex gap-2.5 px-3 py-2",
        highlight && "bg-primary/5 rounded-lg",
        isCompleted && !highlight && "text-muted-foreground",
        !isCompleted && !highlight && "text-foreground",
        className,
      )}
    >
      {variant === "timeline" ? (
        <div className="relative flex w-4 flex-shrink-0 flex-col items-center">
          <PlanStepStatusIcon status={status} index={index} className="relative z-10 mt-0.5" />
          {!isLast && (
            <div className="border-border absolute top-5 bottom-0 left-1/2 w-px -translate-x-1/2 border-l border-dashed" />
          )}
        </div>
      ) : (
        <PlanStepStatusIcon status={status} index={index} className="relative top-0.5" />
      )}
      <p className="min-w-0 flex-1 break-words text-sm leading-relaxed">{description}</p>
    </div>
  );
}
