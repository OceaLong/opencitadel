"use client";

import { useState } from "react";
import { CheckIcon, ChevronDown, Languages } from "lucide-react";

import { AttachmentsMessage } from "@/components/attachments-message";
import { ManusIcon } from "@/components/manus-icon";
import { MarkdownContent } from "@/components/markdown-content";
import { ToolUse } from "@/components/tool-use";

import type { ToolEvent } from "@/lib/api/types";
import { type AttachmentFile, getToolTimeLabel, type TimelineItem } from "@/lib/session-events";
import { cn } from "@/lib/utils";

export type ChatMessageProps = {
  className?: string;
  item: TimelineItem;
  onViewAllFiles?: () => void;
  onFileClick?: (file: AttachmentFile) => void;
  onToolClick?: (tool: ToolEvent) => void;
};

type ToolRowProps = {
  className?: string;
  timeLabel?: string;
  children: React.ReactNode;
};

function ToolRow({ className, timeLabel, children }: ToolRowProps) {
  const [hovered, setHovered] = useState(false);
  return (
    <div
      className={cn("mt-3 flex w-full min-w-0 items-center justify-between gap-2", className)}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <div className="min-w-0 flex-shrink-0">{children}</div>
      <span
        className={cn(
          "text-muted-foreground min-w-[2.5rem] flex-shrink-0 text-right text-xs transition-opacity duration-150",
          hovered ? "opacity-100" : "opacity-0",
        )}
      >
        {timeLabel ?? "刚刚"}
      </span>
    </div>
  );
}

export function ChatMessage({
  className,
  item,
  onViewAllFiles,
  onFileClick,
  onToolClick,
}: ChatMessageProps) {
  if (item.kind === "user") {
    return (
      <div className={cn("group mt-3 flex w-full flex-col items-end justify-end gap-1", className)}>
        <div className="relative flex max-w-[90%] flex-col items-end gap-2">
          <div className="border-border/70 bg-card text-foreground relative flex items-center overflow-hidden rounded-2xl border px-3.5 py-2.5 text-sm leading-relaxed shadow-[var(--shadow-card)]">
            {item.data.message ?? ""}
          </div>
        </div>
      </div>
    );
  }

  if (item.kind === "assistant") {
    return (
      <div className={cn("group mt-3 flex w-full flex-col gap-2", className)}>
        <div className="group flex h-7 items-center justify-between">
          <div className="text-foreground flex items-center justify-center gap-1">
            <Languages size={18} />
            <ManusIcon />
          </div>
        </div>
        <div className="text-foreground m-0 max-w-none p-0">
          <MarkdownContent content={item.data.message ?? ""} />
        </div>
      </div>
    );
  }

  if (item.kind === "tool") {
    return (
      <ToolRow className={className} timeLabel={item.timeLabel}>
        <ToolUse
          data={item.data}
          onClick={onToolClick ? () => onToolClick(item.data) : undefined}
        />
      </ToolRow>
    );
  }

  if (item.kind === "step") {
    return <StepBlock stepItem={item} className={className} onToolClick={onToolClick} />;
  }

  if (item.kind === "attachments") {
    return (
      <div className={cn("mt-3", className)}>
        <AttachmentsMessage
          role={item.role}
          files={item.files}
          onViewAllFiles={item.role === "assistant" ? onViewAllFiles : undefined}
          onFileClick={onFileClick}
        />
      </div>
    );
  }

  if (item.kind === "error") {
    return (
      <div className={cn("group mt-3 flex w-full flex-col gap-2", className)}>
        <div className="group flex h-7 items-center justify-between">
          <div className="flex items-center justify-center gap-1 text-red-600">
            <Languages size={18} />
            <ManusIcon />
          </div>
        </div>
        <div className="m-0 max-w-none p-0 text-red-600">
          <MarkdownContent content={item.error} />
        </div>
      </div>
    );
  }

  return null;
}

function StepBlock({
  stepItem,
  className,
  onToolClick,
}: {
  stepItem: Extract<TimelineItem, { kind: "step" }>;
  className?: string;
  onToolClick?: (tool: ToolEvent) => void;
}) {
  const [expanded, setExpanded] = useState(true);
  const { data, tools } = stepItem;
  const isCompleted = data.status === "completed";

  return (
    <div className={cn("mt-3 flex flex-col", className)}>
      <div
        role="button"
        tabIndex={0}
        onClick={() => setExpanded(!expanded)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setExpanded((prev) => !prev);
          }
        }}
        className="group/header text-foreground hover:bg-muted/60 focus-visible:ring-ring/40 flex w-full cursor-pointer justify-between gap-2 truncate rounded-lg px-1.5 py-1 text-sm transition-colors outline-none focus-visible:ring-2"
      >
        <div className="flex min-w-0 flex-1 flex-row items-center justify-start gap-2 truncate">
          <div
            className={cn(
              "border-primary/20 bg-primary/75 flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full border shadow-[var(--shadow-card)]",
              !isCompleted && "bg-muted border-border",
            )}
          >
            <CheckIcon className="text-white" size={10} />
          </div>
          <div className="markdown-content min-w-0 truncate font-medium">{data.description}</div>
          <ChevronDown
            className={cn(
              "text-muted-foreground flex-shrink-0 transition-transform",
              expanded && "rotate-180",
            )}
          />
        </div>
      </div>
      {expanded && tools.length > 0 && (
        <div className="flex">
          <div className="relative w-6 flex-shrink-0">
            <div className="border-border absolute top-2 bottom-0 left-[7px] w-[1px] border-l border-dashed" />
          </div>
          <div className="flex min-w-0 flex-1 flex-col gap-3 overflow-hidden pt-2 transition-[max-height,opacity] duration-150 ease-in-out">
            {tools.map((tool, idx) => (
              <ToolRow key={`${data.id}-tool-${idx}`} timeLabel={getToolTimeLabel(tool)}>
                <ToolUse data={tool} onClick={onToolClick ? () => onToolClick(tool) : undefined} />
              </ToolRow>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
