"use client";

import { Bot, FileSearch, Globe, Maximize2, Monitor, Package, Search, Terminal, Wrench } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";

import { ArtifactWorkbench } from "@/components/artifact-workbench";
import { JumpToLatestButton, ToolPreviewContent } from "@/components/tool-preview-renderers";
import type { ToolKind } from "@/components/tool-use/utils";
import { getFriendlyToolLabel, getToolKind } from "@/components/tool-use/utils";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import type { ArtifactEventSummary, ToolEvent } from "@/lib/api/types";
import { formatDuration } from "@/lib/session-events";
import { StatusBadge } from "@/components/status-badge";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export type ToolPreviewPanelProps = {
  sessionId: string;
  tool?: ToolEvent | null;
  artifacts?: ArtifactEventSummary[];
  focusedArtifactId?: string | null;
  onClose: () => void;
  onJumpToLatest?: () => void;
  onOpenVNC?: () => void;
};

function getToolDescription(kind: ToolKind, t: ReturnType<typeof useTranslations<"toolPreview">>): string {
  const map: Record<ToolKind, string> = {
    bash: t("toolTerminal"),
    browser: t("toolBrowser"),
    search: t("toolSearch"),
    file: t("toolFile"),
    mcp: t("toolMcp"),
    a2a: t("toolA2a"),
    message: t("toolMessage"),
    default: t("toolDefault"),
  };
  return map[kind];
}

const TOOL_ICONS: Record<ToolKind, typeof Terminal> = {
  bash: Terminal,
  browser: Globe,
  search: Search,
  file: FileSearch,
  mcp: Wrench,
  a2a: Bot,
  message: Monitor,
  default: Monitor,
};

function formatToolTime(value: ToolEvent["started_at"]): string | null {
  if (value == null) return null;
  const date = new Date(typeof value === "number" && value < 10000000000 ? value * 1000 : value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleTimeString();
}

function formatArgs(args: Record<string, unknown>): string {
  try {
    return JSON.stringify(args || {}, null, 2);
  } catch {
    return "{}";
  }
}

function ToolPreviewHeader({
  tool,
  onClose,
}: {
  tool: ToolEvent;
  onClose: () => void;
}) {
  const t = useTranslations("toolPreview");
  const kind = getToolKind(tool);
  const label = getFriendlyToolLabel(tool);
  const ToolIcon = TOOL_ICONS[kind];
  const toolDesc = getToolDescription(kind, t);
  const startedAt = formatToolTime(tool.started_at);
  const endedAt = formatToolTime(tool.ended_at);

  return (
    <div className="border-border/70 bg-muted/30 flex flex-shrink-0 flex-col gap-2 border-b px-4 py-3">
      <div className="flex items-center justify-between">
        <h2 className="text-foreground text-base font-semibold">{t("computerTitle")}</h2>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={onClose}
          aria-label={t("closePreview")}
          className="cursor-pointer"
        >
          <Maximize2 size={16} />
        </Button>
      </div>
      <div className="text-muted-foreground flex items-center gap-2 text-sm">
        <Monitor size={14} className="text-muted-foreground flex-shrink-0" />
        <span>{t("usingTool")}</span>
        <span className="text-foreground font-medium">{toolDesc}</span>
      </div>
      <div className="border-border/70 bg-muted/60 text-foreground inline-flex w-fit max-w-full items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs">
        <ToolIcon size={14} className="text-muted-foreground flex-shrink-0" />
        <span className="truncate">{label}</span>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {tool.status && (
          <StatusBadge
            variant={
              tool.status === "calling"
                ? "warning"
                : tool.status === "called"
                  ? "success"
                  : "destructive"
            }
            className="uppercase tracking-wide"
          >
            {tool.status === "calling" ? "running" : tool.status}
          </StatusBadge>
        )}
        {tool.duration_ms != null && (
          <span className="text-muted-foreground">
            {t("duration", { duration: formatDuration(tool.duration_ms) ?? "" })}
          </span>
        )}
        {startedAt && (
          <span className="text-muted-foreground">
            {startedAt}
            {endedAt ? ` - ${endedAt}` : ""}
          </span>
        )}
      </div>
      {tool.error && (
        <div className="rounded-lg border border-red-500/30 bg-red-500/10 px-2.5 py-1.5 text-xs text-red-700 dark:text-red-300">
          {tool.error}
        </div>
      )}
      {tool.args && Object.keys(tool.args).length > 0 && (
        <details className="border-border/70 bg-background/60 rounded-lg border px-2.5 py-1.5 text-xs">
          <summary className="text-muted-foreground cursor-pointer select-none">{t("viewArgs")}</summary>
          <pre className="text-muted-foreground mt-2 max-h-40 overflow-auto font-mono whitespace-pre-wrap">
            {formatArgs(tool.args)}
          </pre>
        </details>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main Component                                                     */
/* ------------------------------------------------------------------ */

export function ToolPreviewPanel({
  sessionId,
  tool,
  artifacts = [],
  focusedArtifactId,
  onClose,
  onJumpToLatest,
  onOpenVNC,
}: ToolPreviewPanelProps) {
  const t = useTranslations("toolPreview");
  const hasArtifacts = artifacts.length > 0;
  const hasTool = !!tool;
  const defaultTab = hasArtifacts && !hasTool ? "artifacts" : hasTool ? "tool" : "artifacts";

  const [activeTab, setActiveTab] = useState<"artifacts" | "tool">(defaultTab);

  useEffect(() => {
    if (focusedArtifactId) {
      setActiveTab("artifacts");
    }
  }, [focusedArtifactId]);

  useEffect(() => {
    if (!hasTool && hasArtifacts) {
      setActiveTab("artifacts");
    } else if (hasTool && !hasArtifacts) {
      setActiveTab("tool");
    }
  }, [hasArtifacts, hasTool]);

  const showTabs = hasArtifacts && hasTool;

  const toolKind = useMemo(() => (tool ? getToolKind(tool) : null), [tool]);

  if (!hasArtifacts && !hasTool) {
    return null;
  }

  return (
    <div className="bg-card flex h-full flex-col overflow-hidden rounded-2xl shadow-[var(--shadow-panel)]">
      {showTabs ? (
        <Tabs
          value={activeTab}
          onValueChange={(value) => setActiveTab(value as "artifacts" | "tool")}
          className="flex h-full flex-col gap-0"
        >
          <div className="border-border/70 flex flex-shrink-0 items-center justify-between border-b px-4 py-2">
            <TabsList variant="line">
              <TabsTrigger value="artifacts" className="gap-1.5">
                <Package className="size-3.5" />
                {t("artifacts")}
              </TabsTrigger>
              <TabsTrigger value="tool" className="gap-1.5">
                <Monitor className="size-3.5" />
                {t("toolPreviewTab")}
              </TabsTrigger>
            </TabsList>
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={onClose}
              aria-label={t("closePreview")}
              className="cursor-pointer"
            >
              <Maximize2 size={16} />
            </Button>
          </div>
          <TabsContent value="artifacts" className="mt-0 flex min-h-0 flex-1 flex-col overflow-hidden">
            <ArtifactWorkbench
              sessionId={sessionId}
              artifacts={artifacts}
              focusedArtifactId={focusedArtifactId}
              className="flex-1"
            />
          </TabsContent>
          <TabsContent value="tool" className="mt-0 flex min-h-0 flex-1 flex-col overflow-hidden">
            {tool && toolKind && (
              <>
                <ToolPreviewHeader tool={tool} onClose={onClose} />
                <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
                  <ToolPreviewContent kind={toolKind} tool={tool} onOpenVNC={onOpenVNC} />
                  {onJumpToLatest && (
                    <div className="absolute bottom-6 left-1/2 z-10 -translate-x-1/2">
                      <JumpToLatestButton onClick={onJumpToLatest} />
                    </div>
                  )}
                </div>
              </>
            )}
          </TabsContent>
        </Tabs>
      ) : hasArtifacts ? (
        <>
          <div className="border-border/70 flex flex-shrink-0 items-center justify-between border-b px-4 py-3">
            <h2 className="text-foreground flex items-center gap-2 text-base font-semibold">
              <Package className="size-4" />
              {t("artifacts")}
            </h2>
            <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label={t("closePreview")}>
              <Maximize2 size={16} />
            </Button>
          </div>
          <ArtifactWorkbench
            sessionId={sessionId}
            artifacts={artifacts}
            focusedArtifactId={focusedArtifactId}
            className="flex-1"
          />
        </>
      ) : (
        tool &&
        toolKind && (
          <>
            <ToolPreviewHeader tool={tool} onClose={onClose} />
            <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
              <ToolPreviewContent kind={toolKind} tool={tool} onOpenVNC={onOpenVNC} />
              {onJumpToLatest && (
                <div className="absolute bottom-6 left-1/2 z-10 -translate-x-1/2">
                  <JumpToLatestButton onClick={onJumpToLatest} />
                </div>
              )}
            </div>
          </>
        )
      )}
    </div>
  );
}
