"use client";

import { executionStatusToRailState, GovernanceRailItem } from "@/components/session/governance-rail";
import { PlanStepStatusIcon } from "@/components/session/plan-step-status-icon";

import type { ExecutionStatus } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export type PlanStepRowProps = {
  description: string;
  status: ExecutionStatus;
  /** 1-based index for pending steps in approval view */
  index?: number;
  highlight?: boolean;
};

export function PlanStepRow({ description, status, index, highlight = false }: PlanStepRowProps) {
  const isCompleted = status === "completed";

  return (
    <GovernanceRailItem
      state={executionStatusToRailState(status)}
      glyph={<PlanStepStatusIcon status={status} index={index} className="relative z-10 mt-0.5" />}
      highlight={highlight}
    >
      <p
        className={cn(
          "px-3 text-sm leading-relaxed break-words",
          isCompleted && !highlight && "text-muted-foreground",
          !isCompleted && !highlight && "text-foreground",
        )}
      >
        {description}
      </p>
    </GovernanceRailItem>
  );
}
