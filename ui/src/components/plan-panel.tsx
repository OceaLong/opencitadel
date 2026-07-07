"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { useTranslations } from "next-intl";

import { PlanProgressHeader } from "@/components/plan-progress-header";
import { PlanStepRow } from "@/components/plan-step-row";
import { PlanStepStatusIcon } from "@/components/plan-step-status-icon";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";

import type { PlanStep } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export type PlanPanelProps = {
  className?: string;
  /** 计划步骤列表（来自事件列表中的 plan 事件） */
  steps?: PlanStep[];
};

function getActiveStep(steps: PlanStep[]): PlanStep | undefined {
  return (
    steps.find((step) => step.status === "running") ??
    steps.find((step) => step.status !== "completed") ??
    steps[steps.length - 1]
  );
}

export function PlanPanel({ className, steps: stepsProp = [] }: PlanPanelProps) {
  const t = useTranslations("planPanel");
  const [isExpanded, setIsExpanded] = useState(false);
  const togglePanel = () => setIsExpanded(!isExpanded);
  const steps = stepsProp;

  const completedCount = useMemo(
    () => steps.filter((step) => step.status === "completed").length,
    [steps],
  );
  const totalCount = steps.length;
  const activeStep = useMemo(() => getActiveStep(steps), [steps]);
  const progressPercent = totalCount > 0 ? (completedCount / totalCount) * 100 : 0;

  if (steps.length === 0) return null;

  return (
    <div
      className={cn(
        "bg-card border-border/70 rounded-xl border shadow-[var(--shadow-card)]",
        className,
      )}
    >
      {!isExpanded ? (
        <Button
          type="button"
          variant="ghost"
          onClick={togglePanel}
          className="hover:bg-muted/40 h-auto w-full justify-between gap-3 rounded-xl px-4 py-2.5"
        >
          <div className="flex min-w-0 flex-1 items-start gap-2.5">
            {activeStep && (
              <PlanStepStatusIcon status={activeStep.status} className="relative top-0.5" />
            )}
            <div className="min-w-0 flex-1 space-y-1.5 text-left">
              <p className="text-foreground break-words text-sm leading-relaxed">
                {activeStep?.description ?? t("noSteps")}
              </p>
              <Progress value={progressPercent} className="h-1" />
            </div>
          </div>
          <div className="flex flex-shrink-0 flex-col items-end gap-1.5">
            <span className="text-muted-foreground text-xs tabular-nums">
              {completedCount} / {totalCount}
            </span>
            <ChevronUp className="text-muted-foreground size-4" />
          </div>
        </Button>
      ) : (
        <div className="flex flex-col gap-3 px-4 py-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-foreground font-semibold">{t("taskProgress")}</span>
            <Button
              type="button"
              onClick={togglePanel}
              variant="ghost"
              size="icon-xs"
              className="cursor-pointer"
            >
              <ChevronDown className="text-muted-foreground size-4" />
            </Button>
          </div>
          <div className="bg-muted/40 rounded-xl py-3">
            <PlanProgressHeader
              title={t("stepsCompleted")}
              completedCount={completedCount}
              totalCount={totalCount}
              className="mb-2"
            />
            <div className="max-h-[min(calc(100vh-360px),400px)] overflow-y-auto">
              {steps.map((step, index) => (
                <PlanStepRow
                  key={step.id}
                  description={step.description}
                  status={step.status}
                  highlight={step.status === "running"}
                  variant="timeline"
                  isLast={index === steps.length - 1}
                />
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
