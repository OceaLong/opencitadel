import { Hexagon } from "lucide-react";
import type { ReactNode } from "react";

import type { ExecutionStatus } from "@/lib/api/types";
import type { ToolEventStatus } from "@/lib/api/types/common";
import { cn } from "@/lib/utils";

export type RailState = "declared" | "running" | "done" | "failed";

export function toolEventRailState(status?: ToolEventStatus): RailState {
  if (status === "calling") return "running";
  if (status === "called") return "done";
  if (status === "error") return "failed";
  return "declared";
}

export function executionStatusToRailState(status: ExecutionStatus): RailState {
  if (status === "completed") return "done";
  if (status === "failed") return "failed";
  if (status === "running") return "running";
  return "declared";
}

const glyphClasses: Record<RailState, string> = {
  declared: "border-border bg-background border-2",
  running: "bg-info animate-pulse",
  done: "bg-primary",
  failed: "bg-destructive",
};

export function GovernanceRailItem({
  state,
  glyph,
  highlight = false,
  children,
}: {
  state: RailState;
  /** Custom glyph replacing the default dot (e.g. `PlanStepStatusIcon`). Horizontally
   * re-centered onto the rail line via `-ml-1` to compensate for its larger footprint. */
  glyph?: ReactNode;
  /** Tints the whole row (glyph + content) `bg-primary/5 rounded-lg` (e.g. the active/running
   * step) — matches the old per-row highlight, which covered the icon too. */
  highlight?: boolean;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "relative flex min-w-0 gap-3",
        highlight && "bg-primary/5 -my-1 rounded-lg py-1",
      )}
    >
      {glyph ? (
        <span
          data-rail-state={state}
          className="z-10 -ml-1 shrink-0 self-start"
          aria-hidden={glyph ? undefined : true}
        >
          {glyph}
        </span>
      ) : (
        <span
          data-rail-state={state}
          className={cn("z-10 mt-2 size-[9px] shrink-0 self-start rounded-full", glyphClasses[state])}
          aria-hidden
        />
      )}
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

/**
 * Compact ⬡ checkpoint-restore trigger. Shared by `GovernanceRail`'s rail-head
 * button and any standalone placement (e.g. StepBlock's header fallback when
 * the rail itself isn't rendered) so both stay visually identical.
 */
export function RailCheckpointButton({
  title,
  onClick,
  disabled,
  className,
}: {
  title?: string;
  onClick: () => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <button
      type="button"
      data-rail-checkpoint
      title={title}
      aria-label={title}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "text-muted-foreground hover:text-foreground bg-background relative z-10 -ml-1 flex size-4 items-center justify-center self-start disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
    >
      <Hexagon className="size-3.5" />
    </button>
  );
}

const railLineClasses: Record<"default" | "completed" | "failed", string> = {
  default: "bg-border",
  completed: "bg-primary/40",
  failed: "bg-destructive/40",
};

export function GovernanceRail({
  children,
  className,
  checkpointTitle,
  onRestoreCheckpoint,
  restoreDisabled,
  lineState = "default",
}: {
  children: ReactNode;
  className?: string;
  checkpointTitle?: string;
  onRestoreCheckpoint?: () => void;
  restoreDisabled?: boolean;
  /** All items in the rail have reached `done` → "completed" dims the rail line; any `error` → "failed" reddens it. */
  lineState?: "default" | "completed" | "failed";
}) {
  return (
    <div className={cn("relative flex flex-col gap-3 pl-1", className)}>
      <span
        data-rail-line
        className={cn("absolute inset-y-1 left-[7px] w-0.5 rounded-full", railLineClasses[lineState])}
        aria-hidden
      />
      {onRestoreCheckpoint ? (
        <RailCheckpointButton title={checkpointTitle} onClick={onRestoreCheckpoint} disabled={restoreDisabled} />
      ) : null}
      {children}
    </div>
  );
}
