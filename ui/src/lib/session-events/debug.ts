import { modelErrorMessage } from "@/lib/api/llm-status";
import type {
  DebugItemEvent,
  EventMeta,
  EventVisibility,
  SSEEventData,
  ToolEvent,
} from "@/lib/api/types";

import { toMillis } from "./format";

export const TRANSIENT_EVENT_TYPES = new Set([
  "message_delta",
  "reasoning_delta",
  "tool_args_delta",
]);

export function getEventVisibility(ev: SSEEventData): EventVisibility {
  const visibility = (ev.data as { visibility?: EventVisibility })?.visibility;
  return visibility ?? (TRANSIENT_EVENT_TYPES.has(ev.type) ? "internal" : "user");
}

function syntheticDebugMeta(): EventMeta {
  return {
    schema_version: 2,
    visibility: "debug",
    channel: "debug",
    persist: false,
    created_at: Math.floor(Date.now() / 1000),
  };
}

/** 判断 assistant 文本是否像 planner 结构化 JSON（历史数据兼容） */
export function looksLikePlannerJson(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed.startsWith("{")) return false;
  try {
    const parsed = JSON.parse(trimmed) as Record<string, unknown>;
    if (!parsed || typeof parsed !== "object") return false;
    if (Array.isArray(parsed.steps)) return true;
    return typeof parsed.title === "string" && typeof parsed.goal === "string";
  } catch {
    return false;
  }
}

/** 从事件列表提取调试项（planner 输出、reasoning 等） */
export function extractDebugItems(events: SSEEventData[]): DebugItemEvent[] {
  const items: DebugItemEvent[] = [];
  const reasoningStreams = new Map<string, string>();
  const toolArgStreams = new Map<string, string>();

  for (const ev of events) {
    if (ev.type === "debug_item") {
      items.push(ev.data as DebugItemEvent);
      continue;
    }
    if (ev.type === "reasoning_delta") {
      const { stream_id, delta } = ev.data as { stream_id: string; delta: string };
      reasoningStreams.set(stream_id, (reasoningStreams.get(stream_id) ?? "") + delta);
      continue;
    }
    if (ev.type === "tool_args_delta") {
      const { tool_call_id, delta } = ev.data as { tool_call_id: string; delta: string };
      toolArgStreams.set(tool_call_id, (toolArgStreams.get(tool_call_id) ?? "") + delta);
    }
  }

  for (const [streamId, content] of reasoningStreams) {
    if (content.trim()) {
      items.push({
        ...syntheticDebugMeta(),
        item_type: "reasoning_summary",
        payload: { stream_id: streamId, content },
      });
    }
  }
  for (const [toolCallId, content] of toolArgStreams) {
    if (content.trim()) {
      items.push({
        ...syntheticDebugMeta(),
        item_type: "tool_args",
        payload: { tool_call_id: toolCallId, content },
      });
    }
  }
  return items;
}

export type SessionErrorItem = {
  id: string;
  message: string;
  rawMessage?: string;
  source: "tool" | "system";
  toolName?: string;
  code?: string | null;
  timestamp?: number;
  repeatCount?: number;
};

function systemErrorMergeKey(
  code: string | null | undefined,
  message: string,
  rawMessage?: string,
): string {
  return `${code ?? ""}\0${message}\0${rawMessage ?? ""}`;
}

export function countSessionErrorOccurrences(items: SessionErrorItem[]): number {
  return items.reduce((total, item) => total + (item.repeatCount ?? 1), 0);
}

/** 从事件列表提取去重后的错误项（tool.error 与 type: error） */
export function extractSessionErrors(events: SSEEventData[]): SessionErrorItem[] {
  const items: SessionErrorItem[] = [];

  for (const ev of events) {
    if (ev.type === "error") {
      const errorData = ev.data as {
        error?: string;
        code?: string | null;
        created_at?: number;
        event_id?: string;
      };
      const message = modelErrorMessage(errorData.code) ?? errorData.error;
      if (!message) continue;
      const rawMessage = errorData.error;
      const timestamp = toMillis(errorData.created_at);
      const mergeKey = systemErrorMergeKey(errorData.code, message, rawMessage);
      const last = items[items.length - 1];
      if (
        last?.source === "system" &&
        systemErrorMergeKey(last.code, last.message, last.rawMessage) === mergeKey
      ) {
        last.repeatCount = (last.repeatCount ?? 1) + 1;
        if (timestamp !== undefined) {
          last.timestamp = timestamp;
        }
        continue;
      }
      const key = errorData.event_id ? `error:${errorData.event_id}` : `error:${items.length}`;
      items.push({
        id: key,
        message,
        rawMessage,
        source: "system",
        code: errorData.code,
        timestamp,
        repeatCount: 1,
      });
      continue;
    }

    if (ev.type === "tool") {
      const tool = ev.data as ToolEvent & { event_id?: string; created_at?: number };
      if (!tool.error) continue;
      const key = tool.tool_call_id
        ? `tool:${tool.tool_call_id}`
        : tool.event_id
          ? `tool:${tool.event_id}`
          : `tool:${tool.name}:${tool.function}:${items.length}`;
      items.push({
        id: key,
        message: tool.error,
        source: "tool",
        toolName: tool.name,
        timestamp: toMillis(tool.created_at ?? tool.ended_at ?? tool.started_at),
      });
    }
  }

  return items;
}
