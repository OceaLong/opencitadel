"use client";

import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, type RefObject } from "react";

import { ChatMessage } from "@/components/chat-message";
import type { SessionCheckpoint, SessionDetail, ToolEvent } from "@/lib/api/types";
import type { AttachmentFile, TimelineItem } from "@/lib/session-events";

type VirtualizedTimelineProps = {
  timeline: TimelineItem[];
  scrollContainerRef: RefObject<HTMLDivElement | null>;
  sessionStatus?: SessionDetail["status"];
  onViewAllFiles: () => void;
  onFileClick: (file: AttachmentFile) => void;
  onToolClick: (tool: ToolEvent) => void;
  onClarifyAnswer: (answer: string) => void;
  resolveCheckpoint: (anchorEventId?: string) => SessionCheckpoint | undefined;
  onRestoreCheckpoint: (checkpoint: SessionCheckpoint) => void;
  restoringCheckpoint: boolean;
  streaming?: boolean;
  onSourceClick?: (path: string, line?: number) => void;
};

export function VirtualizedTimeline({
  timeline,
  scrollContainerRef,
  sessionStatus,
  onViewAllFiles,
  onFileClick,
  onToolClick,
  onClarifyAnswer,
  resolveCheckpoint,
  onRestoreCheckpoint,
  restoringCheckpoint,
  streaming,
  onSourceClick,
}: VirtualizedTimelineProps) {
  const virtualizer = useVirtualizer({
    count: timeline.length,
    getScrollElement: () => scrollContainerRef.current,
    estimateSize: () => 120,
    overscan: 8,
    measureElement: (element) => element.getBoundingClientRect().height,
  });

  useEffect(() => {
    if (!streaming || timeline.length === 0) return;
    virtualizer.scrollToIndex(timeline.length - 1, { align: "end", behavior: "auto" });
  }, [streaming, timeline.length, virtualizer]);

  return (
    <div
      className="relative w-full"
      style={{ height: `${virtualizer.getTotalSize()}px` }}
    >
      {virtualizer.getVirtualItems().map((virtualItem) => {
        const item = timeline[virtualItem.index];
        return (
          <div
            key={item.id}
            ref={virtualizer.measureElement}
            data-index={virtualItem.index}
            className="absolute top-0 left-0 w-full"
            style={{ transform: `translateY(${virtualItem.start}px)` }}
          >
            <ChatMessage
              item={item}
              onViewAllFiles={onViewAllFiles}
              onFileClick={onFileClick}
              onToolClick={onToolClick}
              onClarifyAnswer={onClarifyAnswer}
              onSourceClick={onSourceClick}
              sessionStatus={sessionStatus}
              checkpoint={
                item.kind === "user" || item.kind === "step"
                  ? resolveCheckpoint(item.anchorEventId)
                  : undefined
              }
              onRestoreCheckpoint={onRestoreCheckpoint}
              restoringCheckpoint={restoringCheckpoint}
            />
          </div>
        );
      })}
    </div>
  );
}
