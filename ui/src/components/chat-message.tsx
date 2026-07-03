"use client";

import { memo, useState } from "react";
import {
  AlertCircle,
  CheckIcon,
  ChevronDown,
  Clock,
  History,
  Languages,
  Loader2,
} from "lucide-react";
import { useTranslations } from "next-intl";

import { AttachmentsMessage } from "@/components/attachments-message";
import { ClarifyQuestions } from "@/components/clarify-questions";
import { OpenCitadelIcon } from "@/components/open-citadel-icon";
import { MarkdownContent } from "@/components/markdown-content";
import { ToolUse } from "@/components/tool-use";

import type { ClarifyAnswer, SessionCheckpoint, SessionStatus, ToolEvent } from "@/lib/api/types";
import { type AttachmentFile, getToolTimeLabel, type TimelineItem } from "@/lib/session-events";
import { cn } from "@/lib/utils";

export type ChatMessageProps = {
  className?: string;
  item: TimelineItem;
  onViewAllFiles?: () => void;
  onFileClick?: (file: AttachmentFile) => void;
  onToolClick?: (tool: ToolEvent) => void;
  onClarifyAnswer?: (answer: string, clarifyAnswers: ClarifyAnswer[]) => Promise<void> | void;
  sessionStatus?: SessionStatus;
  checkpoint?: SessionCheckpoint;
  onRestoreCheckpoint?: (checkpoint: SessionCheckpoint) => Promise<void> | void;
  restoringCheckpoint?: boolean;
  onSourceClick?: (path: string, line?: number) => void;
};

type ToolRowProps = {
  className?: string;
  timeLabel?: string;
  children: React.ReactNode;
};

function RestoreCheckpointButton({
  checkpoint,
  onRestore,
  disabled,
}: {
  checkpoint: SessionCheckpoint;
  onRestore?: (checkpoint: SessionCheckpoint) => Promise<void> | void;
  disabled?: boolean;
}) {
  const t = useTranslations("chatMessage");
  if (!onRestore) return null;

  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => onRestore(checkpoint)}
      className="text-muted-foreground hover:text-foreground border-border/70 bg-card/80 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-50"
      title={t("restoreTitle")}
    >
      <History className="size-3" />
      <span>{t("restoreHere")}</span>
    </button>
  );
}

function ToolRow({ className, timeLabel, children }: ToolRowProps) {
  const tCommon = useTranslations("common");
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
  onClarifyAnswer,
  sessionStatus,
  checkpoint,
  onRestoreCheckpoint,
  restoringCheckpoint,
  onSourceClick,
}: ChatMessageProps) {
  const t = useTranslations("chatMessage");
  if (item.kind === "user") {
    return (
      <div className={cn("group mt-3 flex w-full flex-col items-end justify-end gap-1", className)}>
        <div className="relative flex max-w-[90%] flex-col items-end gap-2">
          {checkpoint && (
            <RestoreCheckpointButton
              checkpoint={checkpoint}
              onRestore={onRestoreCheckpoint}
              disabled={restoringCheckpoint || sessionStatus === "running"}
            />
          )}
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
            <OpenCitadelIcon />
          </div>
        </div>
        <div className="text-foreground m-0 max-w-none p-0">
          <MarkdownContent content={item.data.message ?? ""} onSourceClick={onSourceClick} />
        </div>
      </div>
    );
  }

  if (item.kind === "clarify") {
    return (
      <div className={cn("group mt-3 flex w-full flex-col gap-2", className)}>
        <div className="group flex h-7 items-center justify-between">
          <div className="text-foreground flex items-center justify-center gap-1">
            <Languages size={18} />
            <OpenCitadelIcon />
          </div>
        </div>
        <ClarifyQuestions
          title={item.title}
          questions={item.questions}
          interactive={item.interactive && sessionStatus === "waiting"}
          onSubmit={onClarifyAnswer}
        />
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

  if (item.kind === "subagent") {
    const statusLabel =
      item.data.status === "completed"
        ? t("completed")
        : item.data.status === "failed"
          ? t("failed")
          : t("running");
    return (
      <div className={cn("mt-3 flex w-full", className)}>
        <div className="border-border/70 bg-muted/30 w-full rounded-lg border px-3 py-2 text-sm">
          <div className="text-muted-foreground mb-1 text-xs">{t("subAgent")} {statusLabel}</div>
          <div className="font-medium">{item.data.goal}</div>
          {item.data.result_preview ? (
            <div className="text-muted-foreground mt-2 text-xs">{item.data.result_preview}</div>
          ) : null}
          {item.data.error ? <div className="mt-1 text-xs text-red-600">{item.data.error}</div> : null}
        </div>
      </div>
    );
  }

  if (item.kind === "step") {
    return (
      <StepBlock
        stepItem={item}
        className={className}
        onToolClick={onToolClick}
        checkpoint={checkpoint}
        onRestoreCheckpoint={onRestoreCheckpoint}
        restoringCheckpoint={restoringCheckpoint}
        sessionStatus={sessionStatus}
      />
    );
  }

  if (item.kind === "wait") {
    return (
      <div className={cn("mt-3 flex w-full", className)}>
        <div className="border-border/70 bg-muted/40 text-muted-foreground flex items-center gap-2 rounded-lg border px-3 py-2 text-sm">
          <Clock className="size-4 shrink-0" />
          <span>{item.message}</span>
        </div>
      </div>
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

  if (item.kind === "error") {
    return (
      <div className={cn("group mt-3 flex w-full flex-col gap-2", className)}>
        <div className="group flex h-7 items-center justify-between">
          <div className="flex items-center justify-center gap-1 text-red-600">
            <Languages size={18} />
            <OpenCitadelIcon />
          </div>
        </div>
        <div className="m-0 max-w-none p-0 text-red-600">
          {item.contextLabel && (
            <div className="mb-1 flex items-center gap-1 text-xs text-red-500">
              <AlertCircle className="size-3.5" />
              <span>{t("errorAfter", { context: item.contextLabel })}</span>
            </div>
          )}
          <MarkdownContent content={item.error} />
        </div>
      </div>
    );
  }

  return null;
}

function toolSignature(tool: ToolEvent): string {
  return [
    (tool as { tool_call_id?: string }).tool_call_id,
    (tool as { status?: string }).status,
    (tool as { function_name?: string }).function_name,
  ].join(":");
}

function itemSignature(item: TimelineItem): string {
  switch (item.kind) {
    case "user":
      return `${item.kind}:${item.id}:${item.data.message ?? ""}`;
    case "assistant":
      return `${item.kind}:${item.id}:${item.data.message ?? ""}`;
    case "clarify":
      return `${item.kind}:${item.id}:${item.interactive}:${item.questions.length}`;
    case "tool":
      return `${item.kind}:${item.id}:${toolSignature(item.data)}`;
    case "step":
      return `${item.kind}:${item.id}:${item.data.status}:${item.tools.length}:${item.tools.map(toolSignature).join("|")}`;
    case "attachments":
      return `${item.kind}:${item.id}:${item.role}:${item.files.map((file) => file.id).join("|")}`;
    case "wait":
      return `${item.kind}:${item.id}:${item.message}`;
    case "error":
      return `${item.kind}:${item.id}:${item.error}:${item.contextLabel ?? ""}`;
    default:
      return "";
  }
}

export const ChatMessage = memo(ChatMessageComponent, (prev, next) => {
  return (
    prev.className === next.className &&
    prev.sessionStatus === next.sessionStatus &&
    prev.onViewAllFiles === next.onViewAllFiles &&
    prev.onFileClick === next.onFileClick &&
    prev.onToolClick === next.onToolClick &&
    prev.onClarifyAnswer === next.onClarifyAnswer &&
    prev.checkpoint?.id === next.checkpoint?.id &&
    prev.onRestoreCheckpoint === next.onRestoreCheckpoint &&
    prev.restoringCheckpoint === next.restoringCheckpoint &&
    itemSignature(prev.item) === itemSignature(next.item)
  );
});

function StepBlock({
  stepItem,
  className,
  onToolClick,
  checkpoint,
  onRestoreCheckpoint,
  restoringCheckpoint,
  sessionStatus,
}: {
  stepItem: Extract<TimelineItem, { kind: "step" }>;
  className?: string;
  onToolClick?: (tool: ToolEvent) => void;
  checkpoint?: SessionCheckpoint;
  onRestoreCheckpoint?: (checkpoint: SessionCheckpoint) => Promise<void> | void;
  restoringCheckpoint?: boolean;
  sessionStatus?: SessionStatus;
}) {
  const [expanded, setExpanded] = useState(true);
  const { data, tools } = stepItem;
  const isCompleted = data.status === "completed";
  const isFailed = data.status === "failed";
  const isRunning = data.status === "running";

  return (
    <div className={cn("mt-3 flex flex-col", className)}>
      {checkpoint && (
        <div className="mb-1 flex justify-start">
          <RestoreCheckpointButton
            checkpoint={checkpoint}
            onRestore={onRestoreCheckpoint}
            disabled={restoringCheckpoint || sessionStatus === "running"}
          />
        </div>
      )}
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
              isFailed && "border-red-500/30 bg-red-500",
            )}
          >
            {isRunning ? (
              <Loader2 className="text-muted-foreground size-2.5 animate-spin" />
            ) : isFailed ? (
              <AlertCircle className="text-white" size={10} />
            ) : (
              <CheckIcon className="text-white" size={10} />
            )}
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
