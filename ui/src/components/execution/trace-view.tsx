"use client";
import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { useVirtualizer } from "@tanstack/react-virtual";

import { Button } from "@/components/ui/button";

import type { StepView } from "@/lib/api/types/execution-view";
import { buildTraceRows, stepInterval, type TraceFilters } from "@/lib/execution-view/trace-layout";

import { TraceRow } from "./trace-row";
export type TraceViewProps = {
  renderIdentity?: { scopeId: string; runId: string; at: string; revision: number; ready: boolean };
  steps: StepView[];
  selection: string | null;
  onSelectStep: (id: string) => void;
  asOf: string | null;
  viewport?: { height?: number; narrow?: boolean };
  exhausted?: boolean;
  complete?: boolean;
  error?: boolean;
  onRetry?: () => void;
};
export function TraceView({
  steps,
  renderIdentity,
  selection,
  onSelectStep,
  asOf,
  viewport,
  exhausted = false,
  complete = false,
  error = false,
  onRetry,
}: TraceViewProps) {
  const t = useTranslations("executionTrace");
  const labels = useTranslations("executionWorkbench");
  const host = useRef<HTMLDivElement>(null),
    scroll = useRef<HTMLDivElement>(null);
  const [narrow, setNarrow] = useState(viewport?.narrow ?? false);
  useEffect(() => {
    if (viewport?.narrow !== undefined || !host.current || typeof ResizeObserver === "undefined")
      return;
    const observer = new ResizeObserver(([entry]) => setNarrow(entry.contentRect.width < 560));
    observer.observe(host.current);
    return () => observer.disconnect();
  }, [viewport?.narrow]);
  const [collapsed, setCollapsed] = useState<{ selection: string | null; keys: Set<string> }>({
    selection,
    keys: new Set(),
  });
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [filters, setFilters] = useState<TraceFilters>({});
  const [treeWidth, setTreeWidth] = useState(280);
  const [zoom, setZoom] = useState(1),
    [pan, setPan] = useState(0);
  const [focused, setFocused] = useState<string | null>(null);
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const pendingFocus = useRef(false);
  const effective = useMemo(() => {
    const result = new Set(expanded);
    const map = new Map(steps.map((s) => [s.step_id, s]));
    const seen = new Set<string>();
    let item = selection ? map.get(selection) : undefined;
    while (item && !seen.has(item.step_id)) {
      seen.add(item.step_id);
      if (item.logical_step_id)
        result.add(
          `logical:${item.logical_step_id}${item.parent_step_id ? `:${item.parent_step_id}` : ""}`,
        );
      if (item.parent_step_id) result.add(item.parent_step_id);
      item = item.parent_step_id ? map.get(item.parent_step_id) : undefined;
    }
    if (collapsed.selection === selection) for (const key of collapsed.keys) result.delete(key);
    return result;
  }, [steps, selection, expanded, collapsed]);
  const rows = useMemo(
    () =>
      buildTraceRows(steps, effective, {
        ...filters,
        exhausted,
        complete,
        collapsed: collapsed.selection === selection ? collapsed.keys : undefined,
      }),
    [steps, effective, filters, exhausted, complete, collapsed, selection],
  );
  const timing = useMemo(() => {
    const intervals = new Map(steps.map((s) => [s.step_id, stepInterval(s, asOf)]));
    let start = Infinity,
      end = -Infinity;
    const work = new Map<string, number>();
    for (const s of steps) {
      const v = intervals.get(s.step_id);
      if (v) {
        start = Math.min(start, v.start);
        end = Math.max(end, v.end);
        if (s.logical_step_id)
          work.set(s.logical_step_id, (work.get(s.logical_step_id) ?? 0) + v.end - v.start);
      }
    }
    return {
      intervals,
      start: Number.isFinite(start) ? start : 0,
      range: Number.isFinite(end - start) && end > start ? end - start : 1,
      work,
    };
  }, [steps, asOf]);
  // TanStack Virtual intentionally owns mutable measurement callbacks.
  // eslint-disable-next-line react-hooks/incompatible-library
  const virtual = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scroll.current,
    estimateSize: (i) => (narrow || rows[i].cycle || rows[i].parentState !== "none" ? 48 : 36),
    getItemKey: (i) => rows[i].key,
    overscan: 8,
    initialRect: { width: 800, height: viewport?.height ?? 480 },
  });
  const revealed = useRef<{
    selection: string;
    index: number;
    offset: number;
    following: boolean;
  } | null>(null);
  useEffect(() => {
    if (!selection) {
      revealed.current = null;
      return;
    }
    const previous = revealed.current?.selection === selection ? revealed.current : null;
    const index = rows.findIndex((row) => !row.summary && row.step.step_id === selection);
    if (index < 0) return;
    const offset = rows
      .slice(0, index)
      .reduce((sum, row) => sum + (narrow || row.cycle || row.parentState !== "none" ? 48 : 36), 0);
    const following = previous?.following ?? true;
    // Same-cut ancestor pages can relocate the same selected attempt. Follow its
    // actual row position until the user deliberately navigates the trace.
    if (following && (!previous || previous.index !== index || previous.offset !== offset))
      virtual.scrollToIndex(index, { align: "auto" });
    revealed.current = { selection, index, offset, following };
  }, [selection, rows, virtual, narrow]);
  function pauseReveal() {
    if (revealed.current?.selection === selection) revealed.current.following = false;
  }
  const virtualRows = virtual.getVirtualItems().slice(0, 200);
  function disclose(key: string, open: boolean) {
    pauseReveal();
    setExpanded((previous) => {
      const next = new Set(previous);
      if (open) next.add(key);
      else next.delete(key);
      return next;
    });
    setCollapsed((previous) => {
      const keys = new Set(previous.selection === selection ? previous.keys : []);
      if (open) keys.delete(key);
      else keys.add(key);
      return { selection, keys };
    });
  }
  const toggle = (key: string) => disclose(key, !rows.find((r) => r.key === key)?.expanded);
  function focus(index: number) {
    pauseReveal();
    const row = rows[index];
    if (!row) return;
    pendingFocus.current = true;
    virtual.scrollToIndex(index, { align: "auto" });
    setFocused(row.key);
    const button = buttons.current.get(row.key);
    if (button) {
      button.focus();
      pendingFocus.current = false;
    }
  }
  function keyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const row = rows[index];
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      focus(Math.max(0, Math.min(rows.length - 1, index + (event.key === "ArrowDown" ? 1 : -1))));
    } else if (event.key === "ArrowRight" && row.hasChildren) {
      event.preventDefault();
      disclose(row.key, true);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      if (row.expanded) {
        disclose(row.key, false);
      } else {
        for (let i = index - 1; i >= 0; i--)
          if (rows[i].depth < row.depth) {
            focus(i);
            break;
          }
      }
    }
  }
  const active = Boolean(
    filters.kind || filters.status || filters.tool || filters.retry || filters.approval,
  );
  const selectedVisible =
    !selection || rows.some((r) => !r.summary && r.step.step_id === selection);
  // Logical focus survives unmounting, but Tab must always target a mounted row.
  const mountedRows = virtualRows.map((item) => rows[item.index]);
  const tabKey =
    mountedRows.find((row) => row.key === focused)?.key ??
    mountedRows.find((row) => !row.summary && row.step.step_id === selection)?.key ??
    mountedRows[0]?.key;
  return (
    <section
      data-testid="trace-view"
      data-native-view="trace"
      data-native-ready={!!renderIdentity?.ready && !error}
      data-public-scope={renderIdentity?.scopeId}
      data-public-run={renderIdentity?.runId}
      data-public-at={renderIdentity?.at}
      data-public-revision={renderIdentity?.revision}
      ref={host}
      className="min-w-0"
      aria-label={t("title")}
    >
      <div className="space-y-2 border-b p-3">
        <h2 className="text-sm font-medium">{t("title")}</h2>
        <p className="text-muted-foreground text-xs">
          {t("subtitle", { at: asOf ?? t("unknownTiming") })}
        </p>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          {(["kind", "status", "tool"] as const).map((field) => (
            <label key={field}>
              {t(field)}{" "}
              <select
                aria-label={t(field)}
                value={filters[field] ?? ""}
                className="bg-background h-9 max-w-40 rounded border px-2"
                onChange={(e) => setFilters((f) => ({ ...f, [field]: e.target.value }))}
              >
                <option value="">{t("all")}</option>
                {[
                  ...new Set(
                    steps
                      .map((s) => (field === "tool" ? s.tool_name : s[field]))
                      .filter((v): v is string => Boolean(v)),
                  ),
                ].map((value) => (
                  <option key={value} value={value}>
                    {field === "status"
                      ? labels(`status.${value}`)
                      : field === "kind"
                        ? t(`kinds.${value}`)
                        : value}
                  </option>
                ))}
              </select>
            </label>
          ))}
          {(["retry", "approval"] as const).map((field) => (
            <label key={field} className="flex h-9 items-center gap-1">
              <input
                type="checkbox"
                checked={Boolean(filters[field])}
                onChange={(e) => setFilters((f) => ({ ...f, [field]: e.target.checked }))}
              />
              {t(field)}
            </label>
          ))}
          {active && (
            <Button variant="ghost" size="sm" onClick={() => setFilters({})}>
              {t("clearFilters")}
            </Button>
          )}
        </div>
        <div role="status" className="text-muted-foreground text-xs">
          {t(exhausted ? (complete ? "complete" : "incomplete") : "partial", {
            count: steps.length,
          })}
          {error && (
            <>
              {" "}
              · {t("loadError")}{" "}
              <Button size="sm" variant="ghost" onClick={onRetry}>
                {t("retryLoad")}
              </Button>
            </>
          )}
          {!selectedVisible && <> · {t("selectionHidden")}</>}
        </div>
        {!narrow && (
          <div className="flex flex-wrap items-center gap-3 text-xs">
            <label>
              {t("treeWidth")}{" "}
              <input
                aria-label={t("treeWidth")}
                type="range"
                min="220"
                max="360"
                value={treeWidth}
                onChange={(e) => setTreeWidth(Number(e.target.value))}
              />
            </label>
            <label>
              {t("zoom")}{" "}
              <input
                aria-label={t("zoom")}
                type="range"
                min="1"
                max="8"
                step="1"
                value={zoom}
                onChange={(e) => setZoom(Number(e.target.value))}
              />
            </label>
            <label>
              {t("pan")}{" "}
              <input
                aria-label={t("pan")}
                type="range"
                min="0"
                max="100"
                value={pan}
                onChange={(e) => setPan(Number(e.target.value))}
              />
            </label>
            <span className="font-mono">
              {t("axis", {
                start: ((timing.range * (1 - 1 / zoom) * pan) / 100 / 1000).toFixed(3),
                end: (
                  ((timing.range * (1 - 1 / zoom) * pan) / 100 + timing.range / zoom) /
                  1000
                ).toFixed(3),
              })}
            </span>
          </div>
        )}
      </div>
      {!rows.length ? (
        <p className="p-4 text-sm">{t(active ? "emptyFilter" : "empty")}</p>
      ) : (
        <div
          ref={scroll}
          onWheelCapture={pauseReveal}
          onTouchMoveCapture={pauseReveal}
          onPointerDownCapture={pauseReveal}
          onKeyDownCapture={(event) => {
            if (["PageUp", "PageDown", "Home", "End", " "].includes(event.key)) pauseReveal();
          }}
          onFocusCapture={(event) => {
            const key = (event.target as HTMLElement).dataset.rowKey;
            if (key) setFocused(key);
          }}
          className="relative overflow-auto"
          style={{ height: viewport?.height ?? 480, maxHeight: 720 }}
          role="tree"
          aria-label={t("title")}
        >
          <div style={{ height: virtual.getTotalSize(), position: "relative" }}>
            {virtualRows.map((v) => {
              const row = rows[v.index];
              return (
                <div
                  key={row.key}
                  data-trace-row
                  role="treeitem"
                  aria-level={row.depth + 1}
                  aria-expanded={row.hasChildren ? row.expanded : undefined}
                  aria-selected={!row.summary && selection === row.step.step_id}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    height: v.size,
                    transform: `translateY(${v.start}px)`,
                  }}
                >
                  <TraceRow
                    row={row}
                    treeWidth={treeWidth}
                    expanded={row.expanded}
                    selected={!row.summary && selection === row.step.step_id}
                    tabIndex={tabKey === row.key ? 0 : -1}
                    narrow={viewport?.narrow ?? narrow}
                    interval={row.summary ? null : (timing.intervals.get(row.step.step_id) ?? null)}
                    baseline={timing.start}
                    origin={timing.start + (timing.range * (1 - 1 / zoom) * pan) / 100}
                    range={timing.range / zoom}
                    complete={exhausted && complete}
                    work={
                      row.step.logical_step_id
                        ? (timing.work.get(row.step.logical_step_id) ?? null)
                        : null
                    }
                    onToggle={() => toggle(row.key)}
                    onSelect={() => onSelectStep(row.step.step_id)}
                    onKeyDown={(e) => keyDown(e, v.index)}
                    buttonRef={(node) => {
                      if (node) {
                        buttons.current.set(row.key, node);
                        if (pendingFocus.current && focused === row.key) {
                          node.focus();
                          pendingFocus.current = false;
                        }
                      } else buttons.current.delete(row.key);
                    }}
                  />
                </div>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}
