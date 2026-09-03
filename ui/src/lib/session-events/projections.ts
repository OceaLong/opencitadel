/** Public Run events reduced to the user-facing conversation timeline. */

import { modelErrorMessage } from "@/lib/api/inference-errors";
import type { ChatMessage, SessionFile, SSEEventData, ToolEvent } from "@/lib/api/types";

import type { Locale } from "@/i18n/routing";
import { translate } from "@/i18n/translate";

import { getToolTimeLabel, stableId, toMillis } from "./format";

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

/**
 * 增量构建 timeline 的中间状态。
 *
 * `eventsToTimeline` 与 event-store 的增量投影共用同一份 `foldTimelineEvent`
 * 折叠逻辑，避免两处实现漂移。
 */
export type TimelineBuildState = {
  timeline: TimelineItem[];
  toolIndexes: Map<string, number>;
  messageIndex: number;
  toolIndex: number;
  errorIndex: number;
};

export function createTimelineBuildState(): TimelineBuildState {
  return {
    timeline: [],
    toolIndexes: new Map<string, number>(),
    messageIndex: 0,
    toolIndex: 0,
    errorIndex: 0,
  };
}

/** 将单条事件折叠进 timeline 状态（原 `eventsToTimeline` 循环体）。 */
export function foldTimelineEvent(
  state: TimelineBuildState,
  event: SSEEventData,
  locale?: Locale,
): void {
  const { timeline, toolIndexes } = state;

  if (event.type === "message") {
    const message = event.data;
    if (message.role !== "user" && message.role !== "assistant") return;
    const anchorEventId = message.event_id;
    if (message.role === "user") {
      timeline.push({
        kind: "user",
        id: stableId("user", state.messageIndex++, anchorEventId || String(timeline.length)),
        data: message,
        anchorEventId,
      });
    } else {
      timeline.push({
        kind: "assistant",
        id: stableId("assistant", state.messageIndex++, anchorEventId || String(timeline.length)),
        data: message,
      });
    }
    if (message.attachments?.length) {
      timeline.push({
        kind: "attachments",
        id: stableId("attachments", state.messageIndex, message.role),
        role: message.role,
        files: message.attachments.map(chatAttachmentToDisplay),
      });
    }
    return;
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
        id: stableId("tool", state.toolIndex++, callId || event.data.name),
        data: event.data,
        timeLabel: getToolTimeLabel(event.data, locale),
      });
      if (callId) toolIndexes.set(callId, index);
    }
    return;
  }

  if (event.type === "session_status" && event.data.status === "cancelled") {
    timeline.push({
      kind: "assistant",
      id: stableId("system", state.messageIndex++, event.data.event_id || "cancelled"),
      data: {
        ...event.data,
        role: "assistant",
        message: translate("sessionDetail.taskCancelledNotice", undefined, locale),
      },
    });
    return;
  }

  if (event.type === "error") {
    const error =
      modelErrorMessage(event.data.code, locale) ?? translate("errors.appError", undefined, locale);
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
        id: stableId("error", state.errorIndex++, event.data.event_id || String(timeline.length)),
        error,
        timestamp: toMillis(event.data.created_at),
        repeatCount: 1,
        incidentId: event.data.incident_id,
      });
    }
  }
}

export function eventsToTimeline(events: SSEEventData[], locale?: Locale): TimelineItem[] {
  const state = createTimelineBuildState();
  for (const event of events) foldTimelineEvent(state, event, locale);
  return state.timeline;
}

/** 增量构建任务观测摘要的中间状态。 */
export type ObservationBuildState = {
  startedAt?: number;
  endedAt?: number;
  toolCount: number;
  errorCount: number;
  seenTools: Set<string>;
};

export function createObservationState(): ObservationBuildState {
  return { toolCount: 0, errorCount: 0, seenTools: new Set<string>() };
}

/** 将单条事件折叠进观测摘要状态（原 `getTaskObservationSummary` 循环体）。 */
export function foldObservationEvent(state: ObservationBuildState, event: SSEEventData): void {
  const createdAt = toMillis(event.data.created_at);
  if (createdAt !== undefined) {
    state.startedAt =
      state.startedAt === undefined ? createdAt : Math.min(state.startedAt, createdAt);
    state.endedAt = state.endedAt === undefined ? createdAt : Math.max(state.endedAt, createdAt);
  }
  if (event.type === "tool") {
    const key =
      event.data.tool_call_id || event.data.event_id || `${event.data.name}:${state.toolCount}`;
    if (!state.seenTools.has(key)) {
      state.seenTools.add(key);
      state.toolCount += 1;
    }
    if (event.data.error) state.errorCount += 1;
  } else if (event.type === "error") {
    state.errorCount += 1;
  }
}

export function finalizeObservation(
  state: ObservationBuildState,
  sessionStatus?: string,
): TaskObservationSummary {
  const { startedAt, endedAt, toolCount, errorCount } = state;
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

/**
 * 投影提供者：event-store 为一份 events 快照实现增量 timeline / 观测摘要。
 *
 * `getIncrementalTimeline` 与 `getTaskObservationSummary` 通过 events 数组引用在
 * 注册表中查到对应 store，从而复用 store 的增量投影，避免每次全量重算。
 * 若某份 events 数组不来自 store（如 SSR 初始空数组或直接构造），则回退到纯函数实现。
 */
export type ProjectionProvider = {
  getTimeline(locale?: Locale): TimelineItem[];
  getObservation(sessionStatus?: string): TaskObservationSummary;
};

const providerByEvents = new WeakMap<readonly SSEEventData[], ProjectionProvider>();

export function registerProjectionProvider(
  events: readonly SSEEventData[],
  provider: ProjectionProvider,
): void {
  providerByEvents.set(events, provider);
}

export function getIncrementalTimeline(events: SSEEventData[], locale?: Locale): TimelineItem[] {
  const provider = providerByEvents.get(events);
  return provider ? provider.getTimeline(locale) : eventsToTimeline(events, locale);
}

export function getTaskObservationSummary(
  events: SSEEventData[],
  sessionStatus?: string,
): TaskObservationSummary {
  const provider = providerByEvents.get(events);
  if (provider) return provider.getObservation(sessionStatus);
  const state = createObservationState();
  for (const event of events) foldObservationEvent(state, event);
  return finalizeObservation(state, sessionStatus);
}
