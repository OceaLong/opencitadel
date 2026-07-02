"use client";

import { Bot, FileSearch, Globe, Maximize2, Monitor, Search, Terminal, Wrench } from "lucide-react";
import { useTranslations } from "next-intl";

import { JumpToLatestButton, ToolPreviewContent } from "@/components/tool-preview-renderers";
import type { ToolKind } from "@/components/tool-use/utils";
import { getFriendlyToolLabel, getToolKind } from "@/components/tool-use/utils";
import { Button } from "@/components/ui/button";

import type { ToolEvent } from "@/lib/api/types";
import { formatDuration } from "@/lib/session-events";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export type ToolPreviewPanelProps = {
  tool: ToolEvent;
  onClose: () => void;
  onJumpToLatest?: () => void;
  onOpenVNC?: () => void;
};

function getToolDescription(kind: ToolKind): string {
  const map: Record<ToolKind, string> = {
    bash: "终端",
    browser: "浏览器",
    search: "搜索",
    file: "文件",
    mcp: "MCP 服务",
    a2a: "A2A 智能体",
    message: "消息",
    default: "工具",
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

/* ------------------------------------------------------------------ */
/*  Main Component                                                     */
/* ------------------------------------------------------------------ */

export function ToolPreviewPanel({
  tool,
  onClose,
  onJumpToLatest,
  onOpenVNC,
}: ToolPreviewPanelProps) {
  const t = useTranslations("toolPreview");
  const kind = getToolKind(tool);
  const label = getFriendlyToolLabel(tool);
  const ToolIcon = TOOL_ICONS[kind];
  const toolDesc = getToolDescription(kind);
  const startedAt = formatToolTime(tool.started_at);
  const endedAt = formatToolTime(tool.ended_at);

  return (
    <div className="bg-card flex h-full flex-col overflow-hidden rounded-2xl shadow-[var(--shadow-panel)]">
      {/* Header */}
      <div className="border-border/70 bg-muted/30 flex flex-shrink-0 flex-col gap-2 border-b px-4 py-3">
        <div className="flex items-center justify-between">
          <h2 className="text-foreground text-base font-semibold">{t("computerTitle")}</h2>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={onClose}
            aria-label="关闭预览"
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
            <span
              className={cn(
                "rounded-full border px-2 py-0.5 uppercase tracking-wide",
                tool.status === "calling" &&
                  "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
                tool.status === "called" &&
                  "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
                tool.status === "error" &&
                  "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-300",
              )}
            >
              {tool.status === "calling" ? "running" : tool.status}
            </span>
          )}
          {tool.duration_ms != null && (
            <span className="text-muted-foreground">耗时 {formatDuration(tool.duration_ms)}</span>
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
            <summary className="text-muted-foreground cursor-pointer select-none">查看参数</summary>
            <pre className="text-muted-foreground mt-2 max-h-40 overflow-auto font-mono whitespace-pre-wrap">
              {formatArgs(tool.args)}
            </pre>
          </details>
        )}
      </div>

      {/* Content with overlaid jump button */}
      <div className="relative flex-1 overflow-hidden">
        <ToolPreviewContent kind={kind} tool={tool} onOpenVNC={onOpenVNC} />

        {/* "跳转实时" overlaid at bottom-center */}
        {onJumpToLatest && (
          <div className="absolute bottom-6 left-1/2 z-10 -translate-x-1/2">
            <JumpToLatestButton onClick={onJumpToLatest} />
          </div>
        )}
      </div>
    </div>
  );
}
