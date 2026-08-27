"use client";

import { type RefObject, useEffect } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";

import { ChatMessage } from "@/components/session/chat-message";

import type { ToolEvent } from "@/lib/api/types";
import type { AttachmentFile, TimelineItem } from "@/lib/session-events";

type VirtualizedTimelineProps = {
  timeline: TimelineItem[];
  scrollContainerRef: RefObject<HTMLDivElement | null>;
  onViewAllFiles: () => void;
  onFileClick: (file: AttachmentFile) => void;
  onToolClick: (tool: ToolEvent) => void;
  streaming?: boolean;
  onSourceClick?: (path: string, line?: number) => void;
};

export function VirtualizedTimeline({
  timeline,
  scrollContainerRef,
  onViewAllFiles,
  onFileClick,
  onToolClick,
  streaming,
  onSourceClick,
}: VirtualizedTimelineProps) {
  // TanStack Virtual intentionally owns mutable measurement callbacks.
  // eslint-disable-next-line react-hooks/incompatible-library
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
    <div className="relative w-full" style={{ height: `${virtualizer.getTotalSize()}px` }}>
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
              onSourceClick={onSourceClick}
            />
          </div>
        );
      })}
    </div>
  );
}
