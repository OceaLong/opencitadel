/** Public Run events reduced to the user-facing conversation timeline. */

import { modelErrorMessage } from "@/lib/api/inference-errors";
import type { ChatMessage, SessionFile, SSEEventData, ToolEvent } from "@/lib/api/types";

import type { Locale } from "@/i18n/routing";
import { translate } from "@/i18n/translate";

import { getToolTimeLabel, stableId, toMillis } from "./session-events/format";

export * from "./session-events/format";
export * from "./session-events/normalize";

export type TimelineItem =
  | { kind: "user"; id: string; data: ChatMessage; anchorEventId?: string }
  | { kind: "attachments"; id: string; role: "user" | "assistant"; files: AttachmentFile[] }
  | { kind: "assistant"; id: string; data: ChatMessage }
  | { kind: "tool"; id: string; data: ToolEvent; timeLabel?: string }
  | {
      kind: "error";
      id: string;
      error: string;
      timestamp?: number;
      repeatCount?: number;
      incidentId?: string | null;
    };

export type TaskObservationSummary = {
  startedAt?: number;
  endedAt?: number;
  durationMs?: number;
  toolCount: number;
  errorCount: number;
};

export type AttachmentFile = {
  id: string;
  filename: string;
  extension: string;
  size: number;
  sizeLabel?: string;
};

export function sessionFileToAttachment(file: SessionFile): AttachmentFile {
  return {
    id: file.id,
    filename: file.filename,
    extension: file.extension,
    size: file.size,
  };
}

function chatAttachmentToDisplay(attachment: {
  file_id?: string;
  id?: string;
  filename: string;
  size?: number;
}): AttachmentFile {
  return {
    id: attachment.file_id || attachment.id || "",
    filename: attachment.filename,
    extension: attachment.filename.split(".").pop() || "",
    size: attachment.size ?? 0,
  };
}

export function eventsToTimeline(events: SSEEventData[], locale?: Locale): TimelineItem[] {
  const timeline: TimelineItem[] = [];
  const toolIndexes = new Map<string, number>();
  let messageIndex = 0;
  let toolIndex = 0;
  let errorIndex = 0;

  for (const event of events) {
    if (event.type === "message") {
      const message = event.data;
      if (message.role !== "user" && message.role !== "assistant") continue;
      const anchorEventId = message.event_id;
      if (message.role === "user") {
        timeline.push({
          kind: "user",
          id: stableId("user", messageIndex++, anchorEventId || String(timeline.length)),
          data: message,
          anchorEventId,
        });
      } else {
        timeline.push({
          kind: "assistant",
          id: stableId("assistant", messageIndex++, anchorEventId || String(timeline.length)),
          data: message,
        });
      }
      if (message.attachments?.length) {
        timeline.push({
          kind: "attachments",
          id: stableId("attachments", messageIndex, message.role),
          role: message.role,
          files: message.attachments.map(chatAttachmentToDisplay),
        });
      }
      continue;
    }

    if (event.type === "tool") {
      const callId = event.data.tool_call_id;
      const existingIndex = callId ? toolIndexes.get(callId) : undefined;
      if (existingIndex !== undefined) {
        timeline[existingIndex] = {
          kind: "tool",
          id: timeline[existingIndex].id,
          data: event.data,
          timeLabel: getToolTimeLabel(event.data, locale),
        };
      } else {
        const index = timeline.length;
        timeline.push({
          kind: "tool",
          id: stableId("tool", toolIndex++, callId || event.data.name),
          data: event.data,
          timeLabel: getToolTimeLabel(event.data, locale),
        });
        if (callId) toolIndexes.set(callId, index);
      }
      continue;
    }

    if (event.type === "session_status" && event.data.status === "cancelled") {
      timeline.push({
        kind: "assistant",
        id: stableId("system", messageIndex++, event.data.event_id || "cancelled"),
        data: {
          ...event.data,
          role: "assistant",
          message: translate("sessionDetail.taskCancelledNotice", undefined, locale),
        },
      });
      continue;
    }

    if (event.type === "error") {
      const error =
        modelErrorMessage(event.data.code, locale) ??
        translate("errors.appError", undefined, locale);
      const previous = timeline[timeline.length - 1];
      if (
        previous?.kind === "error" &&
        previous.error === error &&
        (previous.incidentId ?? null) === (event.data.incident_id ?? null)
      ) {
        timeline[timeline.length - 1] = {
          ...previous,
          repeatCount: (previous.repeatCount ?? 1) + 1,
          timestamp: toMillis(event.data.created_at) ?? previous.timestamp,
        };
      } else {
        timeline.push({
          kind: "error",
          id: stableId("error", errorIndex++, event.data.event_id || String(timeline.length)),
          error,
          timestamp: toMillis(event.data.created_at),
          repeatCount: 1,
          incidentId: event.data.incident_id,
        });
      }
    }
  }

  return timeline;
}

export function getTaskObservationSummary(
  events: SSEEventData[],
  sessionStatus?: string,
): TaskObservationSummary {
  let startedAt: number | undefined;
  let endedAt: number | undefined;
  let toolCount = 0;
  let errorCount = 0;
  const seenTools = new Set<string>();

  for (const event of events) {
    const createdAt = toMillis(event.data.created_at);
    if (createdAt !== undefined) {
      startedAt = startedAt === undefined ? createdAt : Math.min(startedAt, createdAt);
      endedAt = endedAt === undefined ? createdAt : Math.max(endedAt, createdAt);
    }
    if (event.type === "tool") {
      const key =
        event.data.tool_call_id || event.data.event_id || `${event.data.name}:${toolCount}`;
      if (!seenTools.has(key)) {
        seenTools.add(key);
        toolCount += 1;
      }
      if (event.data.error) errorCount += 1;
    } else if (event.type === "error") {
      errorCount += 1;
    }
  }

  const durationEnd =
    sessionStatus === "running" && startedAt !== undefined ? Date.now() : (endedAt ?? startedAt);
  return {
    startedAt,
    endedAt,
    durationMs:
      startedAt !== undefined && durationEnd !== undefined
        ? Math.max(0, durationEnd - startedAt)
        : undefined,
    toolCount,
    errorCount,
  };
}
