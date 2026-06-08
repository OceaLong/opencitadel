"use client";

import { useState } from "react";
import { Check, ChevronDown, ChevronUp, Clock } from "lucide-react";

import { Button } from "@/components/ui/button";

import type { PlanStep } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export type PlanPanelProps = {
  className?: string;
  /** 计划步骤列表（来自事件列表中的 plan 事件） */
  steps?: PlanStep[];
};

export function PlanPanel({ className, steps: stepsProp = [] }: PlanPanelProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const togglePanel = () => setIsExpanded(!isExpanded);
  const steps = stepsProp;

  if (steps.length === 0) return null;

  const completedCount = steps.filter((s) => s.status === "completed").length;
  const totalCount = steps.length;

  return (
    <div
      className={cn(
        "bg-card border-border/70 rounded-xl border shadow-[var(--shadow-card)]",
        className,
      )}
    >
      {/* 折叠状态 */}
      {!isExpanded && (
        <div
          className="clickable relative flex cursor-pointer flex-row items-start justify-between rounded-xl pr-3"
          onClick={togglePanel}
        >
          {/* 左侧的最新计划 */}
          <div className="relative min-w-0 flex-1 overflow-hidden">
            <div className="h-9 w-full">
              <div className="text-muted-foreground flex w-full items-center justify-center gap-2.5 truncate px-4 py-2">
                <Clock size={16} />
                <div className="flex w-full flex-col gap-0.5 truncate">
                  <div className="truncate text-sm">{steps[0]?.description ?? "暂无步骤"}</div>
                </div>
              </div>
            </div>
          </div>
          {/* 右侧操作按钮&步骤信息 */}
          <div className="flex h-full flex-shrink-0 items-center justify-center gap-2 py-2.5">
            <span className="text-muted-foreground text-xs">
              {completedCount} / {totalCount}
            </span>
            <ChevronUp className="text-foreground" size={16} />
          </div>
        </div>
      )}
      {/* 展开状态 */}
      {isExpanded && (
        <div className="flex flex-col rounded-xl py-4">
          <div className="mb-4 flex w-full px-4">
            <div className="ml-auto flex items-start">
              <div className="flex items-center justify-center gap-2">
                <Button
                  onClick={togglePanel}
                  variant="ghost"
                  size="icon-xs"
                  className="cursor-pointer"
                >
                  <ChevronDown className="text-muted-foreground" size={16} />
                </Button>
              </div>
            </div>
          </div>
          <div className="px-4">
            <div className="bg-muted/40 rounded-xl px-2 py-3">
              <div className="flex w-full justify-between px-4">
                <span className="text-foreground font-semibold">任务进度</span>
                <div className="flex items-center gap-3">
                  <span className="text-muted-foreground text-xs">
                    {completedCount} / {totalCount}
                  </span>
                </div>
              </div>
              <div className="max-h-[min(calc(100vh-360px),400px)] overflow-y-auto">
                {steps.map((step) => (
                  <div
                    key={step.id}
                    className="text-muted-foreground flex w-full items-center gap-2.5 truncate px-4 py-2 text-sm"
                  >
                    {step.status === "completed" ? (
                      <Check size={16} className="relative top-0.5 flex-shrink-0" />
                    ) : (
                      <Clock size={16} className="relative top-0.5 flex-shrink-0" />
                    )}
                    <div className="flex w-full flex-col truncate">
                      <div className="truncate text-sm">{step.description}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
