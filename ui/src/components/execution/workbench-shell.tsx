"use client";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import type { RunView } from "@/lib/api/types/execution-view";
import type { WorkbenchLayout } from "@/lib/execution-view/layout-preferences";
import type { WorkbenchSelection } from "@/lib/execution-view/state";

export type WorkbenchShellProps = {
  selection: WorkbenchSelection;
  onSelectionChange: (selection: WorkbenchSelection) => void;
  view: ReactNode;
  chat?: ReactNode;
  detail?: ReactNode;
  header?: ReactNode;
  playback?: ReactNode;
  runs?: RunView[];
  runState?: string;
  onRefreshRuns?: () => void;
  onReturnLive?: () => void;
  onLoadMoreRuns?: () => void;
  layout: WorkbenchLayout;
  onLayoutChange: (layout: WorkbenchLayout) => void;
  messageCount?: number;
  clarificationCount?: number;
  detailOpen?: boolean;
  onCloseDetail?: () => void;
  notice?: ReactNode;
};
export function WorkbenchShell({
  selection,
  onSelectionChange,
  view,
  chat,
  detail,
  header,
  playback,
  runs = [],
  runState,
  onRefreshRuns,
  onReturnLive,
  onLoadMoreRuns,
  layout,
  onLayoutChange,
  messageCount = 0,
  clarificationCount = 0,
  detailOpen = Boolean(
    selection.panel || selection.stepId || selection.artifactId || selection.citationId,
  ),
  onCloseDetail,
  notice,
}: WorkbenchShellProps) {
  const t = useTranslations("executionWorkbench");
  const [preferencesOpen, setPreferencesOpen] = useState(false);
  const shortcutLabels = {
    task: t("shortcutTask"),
    debug: t("shortcutDebug"),
    live: t("shortcutLive"),
    conversation: t("shortcutConversation"),
  };
  const shortcuts = { task: "1", debug: "2", live: "l", conversation: "c", ...layout.shortcuts };
  const outer = useRef<HTMLDivElement>(null);
  const [geometry, setGeometry] = useState({ width: 0, desktop: false, mobile: false });
  const [seen, setSeen] = useState(messageCount);
  const conversationOpen = layout.conversationOpen ?? !geometry.mobile;
  const width = layout.detailWidth ?? 400;
  const height = layout.conversationHeight ?? (geometry.desktop ? 264 : 224);
  const resizeConversation = (next: number) =>
    onLayoutChange({ ...layout, conversationHeight: Math.max(184, Math.min(440, next)) });
  const detailOrigin = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (detailOpen)
      detailOrigin.current =
        document.activeElement instanceof HTMLElement ? document.activeElement : null;
  }, [detailOpen]);
  const inline = geometry.desktop && geometry.width >= width + 561;
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const measure = () =>
      setGeometry({
        width: outer.current?.getBoundingClientRect().width ?? 0,
        desktop: window.innerWidth >= 1280,
        mobile: window.innerWidth < 768,
      });
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    if (outer.current) observer?.observe(outer.current);
    window.addEventListener("resize", measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, []);
  const toggle = () => {
    setSeen(messageCount);
    onLayoutChange({ ...layout, conversationOpen: !conversationOpen });
  };
  const close = () => {
    onCloseDetail?.();
    if (!onCloseDetail)
      onSelectionChange({
        ...selection,
        stepId: null,
        panel: null,
        artifactId: null,
        version: null,
        citationId: null,
      });
  };
  const resize = (next: number) =>
    onLayoutChange({
      ...layout,
      detailWidth: Math.max(320, Math.min(640, geometry.width - 561, next)),
    });
  return (
    <Tabs
      value={selection.view}
      onValueChange={(value) =>
        onSelectionChange({ ...selection, view: value as "task" | "debug" })
      }
      asChild
    >
      <div
        ref={outer}
        data-testid="workbench"
        onKeyDown={(event) => {
          if (
            !event.altKey ||
            event.ctrlKey ||
            event.metaKey ||
            event.shiftKey ||
            event.repeat ||
            layout.shortcutsEnabled === false
          )
            return;
          if (
            event.target instanceof Element &&
            event.target.closest(
              'input, textarea, select, [contenteditable="true"], [role="dialog"], [data-vnc]',
            )
          )
            return;
          const key = event.key.toLowerCase();
          const action = (Object.keys(shortcuts) as Array<keyof typeof shortcuts>).find(
            (name) => shortcuts[name] === key,
          );
          if (!action) return;
          event.preventDefault();
          if (action === "task" || action === "debug")
            onSelectionChange({ ...selection, view: action });
          else if (action === "live" && selection.at) onReturnLive?.();
          else if (action === "conversation" && chat) {
            toggle();
            if (conversationOpen) trigger.current?.focus();
          }
        }}
        className="execution-workbench relative flex h-full min-h-0 min-w-0 flex-col overflow-hidden text-sm"
      >
        <header className="bg-background sticky top-0 z-10 shrink-0 space-y-2 border-b p-3 md:px-4">
          {header}
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <label className="flex min-w-0 flex-1 items-center gap-2">
              {t("run")}
              <select
                aria-label={t("run")}
                className="bg-background min-h-9 max-w-full min-w-0 flex-1 rounded-md border px-2 text-xs"
                value={selection.runId}
                onChange={(event) =>
                  onSelectionChange({
                    ...selection,
                    runId: event.target.value,
                    at: null,
                    stepId: null,
                    panel: null,
                    artifactId: null,
                    version: null,
                    citationId: null,
                  })
                }
              >
                {!runs.some((run) => run.run_id === selection.runId) && (
                  <option value={selection.runId}>{selection.runId || t("noRun")}</option>
                )}
                {runs.map((run) => (
                  <option key={run.run_id} value={run.run_id}>
                    {run.admitted_at ?? run.run_id} · {t(`status.${run.status}`)}
                  </option>
                ))}
              </select>
            </label>
            {onRefreshRuns && (
              <Button size="sm" variant="ghost" onClick={onRefreshRuns}>
                {t("refreshRuns")}
              </Button>
            )}
            {onLoadMoreRuns && (
              <Button size="sm" variant="ghost" onClick={onLoadMoreRuns}>
                {t("moreRuns")}
              </Button>
            )}
          </div>
          {runState && runState !== "ready" && (
            <p role="status" className="text-muted-foreground text-xs">
              {t(`listState.${runState}`)}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              aria-label={t("keyboardPreferences")}
              aria-expanded={preferencesOpen}
              onClick={() => setPreferencesOpen(!preferencesOpen)}
            >
              {t("keyboardPreferences")}
            </Button>
            <TabsList aria-label={t("view")}>
              <TabsTrigger value="task">{t("task")}</TabsTrigger>
              <TabsTrigger value="debug">{t("debug")}</TabsTrigger>
            </TabsList>
            <span className="text-muted-foreground text-xs">
              {selection.at ? t("historical") : t("live")}
            </span>
            {chat && (
              <Button
                ref={trigger}
                variant="outline"
                size="sm"
                aria-expanded={conversationOpen}
                onClick={toggle}
              >
                {t("conversation")}
                {!conversationOpen && messageCount > seen
                  ? ` · ${t("newMessages", { count: messageCount - seen })}`
                  : ""}
                {clarificationCount > 0
                  ? ` · ${t("clarifications", { count: clarificationCount })}`
                  : ""}
              </Button>
            )}
          </div>
          {preferencesOpen && (
            <fieldset className="grid gap-2 rounded-md border p-3 sm:grid-cols-2">
              <legend>{t("keyboardPreferences")}</legend>
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  aria-label={t("shortcutsEnabled")}
                  checked={layout.shortcutsEnabled !== false}
                  onChange={(event) =>
                    onLayoutChange({ ...layout, shortcutsEnabled: event.target.checked })
                  }
                />
                {t("shortcutsEnabled")}
              </label>
              <p className="text-muted-foreground text-xs">{t("shortcutHint")}</p>
              {(["task", "debug", "live", "conversation"] as const).map((action) => (
                <label key={action} className="flex items-center justify-between gap-2">
                  {shortcutLabels[action]}
                  <input
                    className="bg-background h-9 w-12 rounded-md border text-center"
                    aria-label={shortcutLabels[action]}
                    maxLength={1}
                    value={shortcuts[action]}
                    onChange={(event) => {
                      const key = event.target.value.toLowerCase();
                      if (
                        /^[a-z0-9]$/.test(key) &&
                        !Object.entries(shortcuts).some(
                          ([name, value]) => name !== action && value === key,
                        )
                      )
                        onLayoutChange({ ...layout, shortcuts: { ...shortcuts, [action]: key } });
                    }}
                  />
                </label>
              ))}
            </fieldset>
          )}
          {notice}
        </header>
        <div
          data-workspace
          className="flex min-h-0 min-w-0 flex-1 overflow-hidden"
          style={{ display: geometry.mobile && conversationOpen ? "none" : undefined }}
        >
          <TabsContent value={selection.view} forceMount asChild>
            <main
              className="min-h-0 min-w-0 flex-1 overflow-y-auto"
              style={inline && detailOpen ? { minWidth: 560 } : undefined}
            >
              {view}
            </main>
          </TabsContent>
          {detailOpen && detail && inline && (
            <>
              <div
                role="separator"
                tabIndex={0}
                aria-label={t("resizeDetail")}
                aria-orientation="vertical"
                aria-valuemin={320}
                aria-valuemax={Math.min(640, geometry.width - 561)}
                aria-valuenow={width}
                className="bg-border focus-visible:bg-ring w-px shrink-0 cursor-col-resize touch-none"
                onKeyDown={(event) => {
                  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                    event.preventDefault();
                    resize(width + (event.key === "ArrowLeft" ? 16 : -16));
                  }
                }}
                onPointerDown={(event) => {
                  event.currentTarget.setPointerCapture(event.pointerId);
                }}
                onPointerMove={(event) => {
                  if (event.currentTarget.hasPointerCapture(event.pointerId))
                    resize((outer.current?.getBoundingClientRect().right ?? 0) - event.clientX);
                }}
                onPointerUp={(event) => event.currentTarget.releasePointerCapture(event.pointerId)}
              />
              <aside
                data-testid="detail-panel"
                aria-label={t("detail")}
                className="min-h-0 shrink-0 overflow-y-auto border-l"
                style={{ width }}
              >
                <Button variant="ghost" size="sm" onClick={close}>
                  {t("closeDetail")}
                </Button>
                {detail}
              </aside>
            </>
          )}
        </div>
        {playback && !(geometry.mobile && conversationOpen) && (
          <div className="shrink-0 border-t">{playback}</div>
        )}
        {chat && !conversationOpen && (
          <div
            data-conversation-bar
            className="bg-background shrink-0 border-t"
            style={{ height: 44 }}
          >
            <Button className="h-full w-full justify-between" variant="ghost" onClick={toggle}>
              {t("conversation")}
              {messageCount > seen ? ` · ${t("newMessages", { count: messageCount - seen })}` : ""}
              {clarificationCount > 0
                ? ` · ${t("clarifications", { count: clarificationCount })}`
                : ""}
            </Button>
          </div>
        )}
        {chat && (
          <section
            data-conversation
            data-mode={geometry.mobile ? "full-height" : "docked"}
            hidden={!conversationOpen}
            className="execution-conversation bg-background min-h-0 shrink-0 border-t"
            style={geometry.mobile ? { height: "auto", flex: "1 1 0%" } : { height }}
          >
            {!geometry.mobile && (
              <div
                role="separator"
                tabIndex={0}
                aria-label={t("resizeConversation")}
                aria-orientation="horizontal"
                aria-valuemin={184}
                aria-valuemax={440}
                aria-valuenow={height}
                className="bg-border focus-visible:bg-ring h-1 shrink-0 cursor-row-resize touch-none"
                onKeyDown={(event) => {
                  if (event.key === "ArrowUp" || event.key === "ArrowDown") {
                    event.preventDefault();
                    resizeConversation(height + (event.key === "ArrowUp" ? 16 : -16));
                  }
                }}
                onPointerDown={(event) => event.currentTarget.setPointerCapture(event.pointerId)}
                onPointerMove={(event) => {
                  if (event.currentTarget.hasPointerCapture(event.pointerId))
                    resizeConversation(
                      (outer.current?.getBoundingClientRect().bottom ?? 0) - event.clientY,
                    );
                }}
                onPointerUp={(event) => event.currentTarget.releasePointerCapture(event.pointerId)}
              />
            )}
            <div className="flex h-9 items-center justify-between border-b px-3">
              <span>{t("conversation")}</span>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  toggle();
                  trigger.current?.focus();
                }}
              >
                {t("collapse")}
              </Button>
            </div>
            <div className="min-h-0 flex-1 overflow-hidden">{chat}</div>
          </section>
        )}
        {detail && !inline && (
          <Sheet
            open={detailOpen}
            onOpenChange={(open) => {
              if (!open) close();
            }}
          >
            <SheetContent
              side="right"
              data-testid="detail-panel"
              onCloseAutoFocus={(event) => {
                if (detailOrigin.current?.isConnected) {
                  event.preventDefault();
                  detailOrigin.current.focus();
                }
              }}
              className="execution-detail-sheet w-full max-w-full overflow-y-auto p-3 sm:w-[88vw] sm:max-w-[560px]"
            >
              <SheetTitle>{t("detail")}</SheetTitle>
              <SheetDescription className="sr-only">{t("detailDescription")}</SheetDescription>
              {detail}
            </SheetContent>
          </Sheet>
        )}
      </div>
    </Tabs>
  );
}
