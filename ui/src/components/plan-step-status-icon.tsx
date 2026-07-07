"use client";

import { AlertCircle, CheckIcon, Loader2 } from "lucide-react";

import type { ExecutionStatus } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export type PlanStepStatusIconProps = {
  status: ExecutionStatus;
  /** 审批阶段显示序号（1-based） */
  index?: number;
  className?: string;
};

export function PlanStepStatusIcon({ status, index, className }: PlanStepStatusIconProps) {
  const isCompleted = status === "completed";
  const isFailed = status === "failed";
  const isRunning = status === "running";

  return (
    <div
      className={cn(
        "border-primary/20 bg-primary/75 flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full border shadow-[var(--shadow-card)]",
        !isCompleted && !isFailed && "bg-muted border-border",
        isFailed && "border-red-500/30 bg-red-500",
        className,
      )}
    >
      {isRunning ? (
        <Loader2 className="text-muted-foreground size-2.5 animate-spin" />
      ) : isFailed ? (
        <AlertCircle className="text-white" size={10} />
      ) : isCompleted ? (
        <CheckIcon className="text-white" size={10} />
      ) : index != null ? (
        <span className="text-muted-foreground text-[10px] font-medium leading-none">{index}</span>
      ) : (
        <span className="bg-muted-foreground/40 size-1.5 rounded-full" />
      )}
    </div>
  );
}
