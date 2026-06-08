"use client";

import { Bot, FileSearch, Globe, Maximize2, Monitor, Search, Terminal, Wrench } from "lucide-react";

import { JumpToLatestButton, ToolPreviewContent } from "@/components/tool-preview-renderers";
import type { ToolKind } from "@/components/tool-use/utils";
import { getFriendlyToolLabel, getToolKind } from "@/components/tool-use/utils";
import { Button } from "@/components/ui/button";

import type { ToolEvent } from "@/lib/api/types";

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

/* ------------------------------------------------------------------ */
/*  Main Component                                                     */
/* ------------------------------------------------------------------ */

export function ToolPreviewPanel({
  tool,
  onClose,
  onJumpToLatest,
  onOpenVNC,
}: ToolPreviewPanelProps) {
  const kind = getToolKind(tool);
  const label = getFriendlyToolLabel(tool);
  const ToolIcon = TOOL_ICONS[kind];
  const toolDesc = getToolDescription(kind);

  return (
    <div className="bg-card flex h-full flex-col overflow-hidden rounded-2xl shadow-[var(--shadow-panel)]">
      {/* Header */}
      <div className="border-border/70 bg-muted/30 flex flex-shrink-0 flex-col gap-2 border-b px-4 py-3">
        <div className="flex items-center justify-between">
          <h2 className="text-foreground text-base font-semibold">MyManus 的电脑</h2>
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
          <span>MyManus 正在使用</span>
          <span className="text-foreground font-medium">{toolDesc}</span>
        </div>
        <div className="border-border/70 bg-muted/60 text-foreground inline-flex w-fit max-w-full items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs">
          <ToolIcon size={14} className="text-muted-foreground flex-shrink-0" />
          <span className="truncate">{label}</span>
        </div>
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
