"use client";

import { memo, useState } from "react";
import { useTranslations } from "next-intl";
import { AlertCircle } from "lucide-react";

import { MarkdownContent } from "@/components/markdown-content";
import { OpenCitadelIcon } from "@/components/open-citadel-icon";
import { AttachmentsMessage } from "@/components/session/attachments-message";
import { ToolUse } from "@/components/tool-use";

import type { ToolEvent } from "@/lib/api/types";
import { type AttachmentFile, type TimelineItem } from "@/lib/session-events";
import { cn } from "@/lib/utils";

export type ChatMessageProps = {
  className?: string;
  item: TimelineItem;
  onViewAllFiles?: () => void;
  onFileClick?: (file: AttachmentFile) => void;
  onToolClick?: (tool: ToolEvent) => void;
  onSourceClick?: (path: string, line?: number) => void;
};

function ToolRow({
  className,
  timeLabel,
  children,
}: {
  className?: string;
  timeLabel?: string;
  children: React.ReactNode;
}) {
  const tCommon = useTranslations("common");
  const [hovered, setHovered] = useState(false);
  return (
    <div
      className={cn("flex w-full min-w-0 items-center justify-between gap-2", className)}
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
        {timeLabel ?? tCommon("justNow")}
      </span>
    </div>
  );
}

function ChatMessageComponent({
  className,
  item,
  onViewAllFiles,
  onFileClick,
  onToolClick,
  onSourceClick,
}: ChatMessageProps) {
  const t = useTranslations("chatMessage");

  if (item.kind === "user") {
    return (
      <div className={cn("group mt-3 flex w-full flex-col items-end gap-1", className)}>
        <div className="border-border/70 bg-card text-foreground shadow-card max-w-[90%] rounded-2xl border px-3.5 py-2.5 text-sm leading-relaxed">
          {item.data.message}
        </div>
      </div>
    );
  }

  if (item.kind === "assistant") {
    return (
      <div className={cn("group mt-3 flex w-full flex-col gap-2", className)}>
        <div className="flex h-7 items-center justify-between">
          <OpenCitadelIcon variant="icon" />
          {item.data.resource_bindings?.length ? (
            <span className="text-muted-foreground text-xs" aria-label={t("resourceVersionsAria")}>
              {item.data.resource_bindings.map((binding) => binding.version_id).join(", ")}
            </span>
          ) : null}
        </div>
        <div className="text-foreground m-0 max-w-none p-0">
          <MarkdownContent content={item.data.message} onSourceClick={onSourceClick} />
        </div>
      </div>
    );
  }

  if (item.kind === "tool") {
    return (
      <ToolRow className={cn("mt-3", className)} timeLabel={item.timeLabel}>
        <ToolUse
          data={item.data}
          onClick={onToolClick ? () => onToolClick(item.data) : undefined}
        />
      </ToolRow>
    );
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

  return (
    <div className={cn("group mt-3 flex w-full flex-col gap-2", className)}>
      <div className="text-destructive flex h-7 items-center gap-1">
        <OpenCitadelIcon variant="icon" />
        <AlertCircle className="size-3.5" />
      </div>
      <div className="text-destructive m-0 max-w-none p-0">
        <MarkdownContent
          content={
            item.repeatCount && item.repeatCount > 1
              ? `${item.error}\n\n(${t("errorRepeated", { count: item.repeatCount })})`
              : item.error
          }
        />
      </div>
    </div>
  );
}

function itemSignature(item: TimelineItem): string {
  if (item.kind === "user" || item.kind === "assistant") {
    return `${item.kind}:${item.id}:${item.data.message}`;
  }
  if (item.kind === "tool") {
    return `${item.kind}:${item.id}:${item.data.tool_call_id}:${item.data.status}`;
  }
  if (item.kind === "attachments") {
    return `${item.kind}:${item.id}:${item.files.map((file) => file.id).join("|")}`;
  }
  return `${item.kind}:${item.id}:${item.error}:${item.repeatCount ?? 1}`;
}

export const ChatMessage = memo(
  ChatMessageComponent,
  (previous, next) =>
    previous.className === next.className &&
    previous.onViewAllFiles === next.onViewAllFiles &&
    previous.onFileClick === next.onFileClick &&
    previous.onToolClick === next.onToolClick &&
    itemSignature(previous.item) === itemSignature(next.item),
);
