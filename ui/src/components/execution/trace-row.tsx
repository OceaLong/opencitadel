"use client";
import { useTranslations } from "next-intl";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { KeyboardEvent, RefCallback } from "react";

import {
  spanWidth,
  type stepInterval,
  type TraceRow as Row,
} from "@/lib/execution-view/trace-layout";

type Props = {
  row: Row;
  treeWidth?: number;
  expanded: boolean;
  selected: boolean;
  tabIndex: number;
  narrow: boolean;
  interval: ReturnType<typeof stepInterval>;
  origin: number;
  baseline: number;
  range: number;
  complete: boolean;
  work: number | null;
  onToggle: () => void;
  onSelect: () => void;
  onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void;
  buttonRef: RefCallback<HTMLButtonElement>;
};
export function TraceRow({
  row,
  treeWidth = 280,
  expanded,
  selected,
  tabIndex,
  narrow,
  interval,
  origin,
  baseline,
  range,
  complete,
  work,
  onToggle,
  onSelect,
  onKeyDown,
  buttonRef,
}: Props) {
  const t = useTranslations("executionTrace");
  const status = useTranslations("executionWorkbench");
  const warning = row.cycle ? "cycle" : row.parentState !== "none" ? row.parentState : null;
  const width = interval ? spanWidth(interval.start, interval.end, range) : null;
  const timing = interval
    ? t("timing", {
        offset: ((interval.start - baseline) / 1000).toFixed(3),
        duration: ((interval.end - interval.start) / 1000).toFixed(3),
      })
    : t("unknownTiming");
  const label = row.step.public_summary || row.step.tool_name || row.step.step_id;
  const tone =
    row.step.status === "failed"
      ? "bg-destructive"
      : row.step.status === "completed"
        ? "bg-success"
        : row.step.status === "waiting" || row.step.kind === "approval"
          ? "bg-warning"
          : "bg-info";
  return (
    <div
      style={narrow ? undefined : { gridTemplateColumns: `${treeWidth}px minmax(0,1fr)` }}
      className={
        narrow
          ? "flex h-full min-w-0 items-center border-b"
          : "grid h-full min-w-0 items-center border-b"
      }
    >
      <div
        className={`flex min-w-0 items-center gap-1 ${row.context ? "text-muted-foreground" : ""}`}
        style={{ paddingLeft: Math.min(row.depth, 12) * 12 }}
      >
        {row.hasChildren ? (
          <button
            type="button"
            data-disclosure
            aria-label={t(expanded ? "collapse" : "expand", { label })}
            aria-expanded={expanded}
            onClick={onToggle}
            className="flex size-9 shrink-0 items-center justify-center rounded focus-visible:ring-2"
          >
            {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          </button>
        ) : (
          <span className="w-9 shrink-0" />
        )}
        <button
          type="button"
          ref={buttonRef}
          data-step={row.summary ? undefined : row.step.step_id}
          data-row-key={row.key}
          tabIndex={tabIndex}
          aria-pressed={selected}
          onKeyDown={onKeyDown}
          onClick={row.summary ? onToggle : onSelect}
          className={`min-w-0 flex-1 truncate rounded px-1 text-left text-xs focus-visible:ring-2 ${selected ? "bg-accent" : ""}`}
          title={`${label} · ${row.step.step_id} · ${row.step.activity_id ?? ""} · ${row.step.attempt_id ?? ""} · ${row.step.invocation_id ?? ""}`}
        >
          <span
            key={JSON.stringify([row.step.projection_revision, label])}
            {...{ elementtiming: "execution-trace-step" }}
            data-native-content="trace-step"
            data-public-run={row.step.run_id}
            data-public-step={row.step.step_id}
            data-public-revision={row.step.projection_revision}
            className="block truncate"
          >
            {row.summary
              ? t("attempts", { count: row.attemptCount ?? 0, total: row.logicalAttemptCount ?? 0 })
              : label}{" "}
            {row.step.attempt_id && !row.summary && (
              <span className="font-mono">{row.step.attempt_id}</span>
            )}
          </span>
          <span className="text-muted-foreground block truncate">
            {row.summary
              ? t("logicalIdentity", { id: row.step.logical_step_id ?? row.step.step_id })
              : status(`status.${row.step.status}`)}
            {warning && ` · ${t(warning)}`}
            {row.filteredDescendantCount > 0 &&
              ` · ${t(complete ? "hidden" : "hiddenPartial", { count: row.filteredDescendantCount })}`}
            {narrow &&
              !row.summary &&
              ` · ${timing}${row.step.wait_reason ? ` · ${row.step.wait_reason}` : ""}`}
            {narrow &&
              row.summary &&
              ` · ${work === null ? t("unknownTiming") : t("knownWork", { duration: (work / 1000).toFixed(3) })}`}
          </span>
        </button>
      </div>
      {!narrow && (
        <div
          data-trace-span
          className="relative h-full min-w-0 overflow-hidden border-l px-2 text-xs"
          aria-label={row.summary ? t("work") : timing}
        >
          {row.summary ? (
            <span className="flex h-full items-center font-mono">
              {work === null
                ? t("unknownTiming")
                : t("knownWork", { duration: (work / 1000).toFixed(3) })}{" "}
              · {t(complete ? "complete" : "partial")}
            </span>
          ) : interval && width !== null ? (
            <>
              <div
                aria-hidden="true"
                className={`border-foreground/30 absolute top-2 h-5 border ${tone} ${interval.open ? "border-r-foreground border-r-2 border-dashed" : ""}`}
                style={{
                  left: `${((interval.start - origin) / range) * 100}%`,
                  width: `${width}%`,
                }}
              />
              <span className="relative flex h-full items-center font-mono">
                {timing}
                {interval.open ? ` · ${t("open")}` : ""}
                {row.step.wait_reason ? ` · ${row.step.wait_reason}` : ""}
              </span>
            </>
          ) : (
            <span className="text-muted-foreground flex h-full items-center">
              {t("unknownTiming")}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
