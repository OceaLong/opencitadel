/**
 * 将 SSE 事件列表转换为时间线展示项与计划步骤
 * 与 chat 流式 / 任务详情接口的响应格式一致
 *
 * 后端事件格式为 { event: "message"|"title"|..., data: {...} }，
 * 前端统一使用 { type, data }，需先归一化。
 */

import type {
  ChatMessage,
  ClarifyQuestion,
  DebugItemEvent,
  EventMeta,
  EventVisibility,
  PlanEvent,
  PlanStep,
  SessionFile,
  SSEEventData,
  SSEEventType,
  StepEvent,
  SubAgentEvent,
  ToolEvent,
} from "@/lib/api/types";
import { translate } from "@/i18n/translate";

const TRANSIENT_EVENT_TYPES = new Set(["message_delta", "reasoning_delta", "tool_args_delta"]);

function getEventVisibility(ev: SSEEventData): EventVisibility {
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
function looksLikePlannerJson(text: string): boolean {
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

/** 后端返回的原始事件（可能用 event 或 type 表示类型） */
type RawEvent = { event?: string; type?: string; data?: unknown };

/**
 * 将后端单条事件转为前端 SSEEventData（统一 type + data）
 */
export function normalizeEvent(raw: RawEvent): SSEEventData | null {
  const type = (raw.type ?? raw.event) as SSEEventType | undefined;
  const data = raw.data;
  if (!type || data === undefined) return null;
  return { type, data } as SSEEventData;
}

/**
 * 将后端事件列表转为前端 SSEEventData[]
 */
export function normalizeEvents(rawList: unknown): SSEEventData[] {
  if (!Array.isArray(rawList)) return [];
  const out: SSEEventData[] = [];
  for (const raw of rawList) {
    const normalized = normalizeEvent(raw as RawEvent);
    if (normalized) out.push(normalized);
  }
  return out;
}

/** 时间线单项：用于渲染对话区的一条记录 */
export type TimelineItem =
  | { kind: "user"; id: string; data: ChatMessage; anchorEventId?: string }
  | { kind: "attachments"; id: string; role: "user" | "assistant"; files: AttachmentFile[] }
  | { kind: "assistant"; id: string; data: ChatMessage }
  | {
      kind: "clarify";
      id: string;
      title?: string | null;
      questions: ClarifyQuestion[];
      interactive: boolean;
    }
  | { kind: "tool"; id: string; data: ToolEvent; timeLabel?: string }
  | { kind: "step"; id: string; data: StepEvent; tools: ToolEvent[]; anchorEventId?: string }
  | {
      kind: "subagent";
      id: string;
      data: SubAgentEvent;
      anchorEventId?: string;
    }
  | { kind: "wait"; id: string; message: string; timestamp?: number }
  | { kind: "error"; id: string; error: string; timestamp?: number; contextLabel?: string };

export type TaskObservationSummary = {
  startedAt?: number;
  endedAt?: number;
  durationMs?: number;
  toolCount: number;
  errorCount: number;
  waitCount: number;
  debugCount: number;
};

/** 附件展示用（文件名、类型、大小等） */
export type AttachmentFile = {
  id: string;
  filename: string;
  extension: string;
  size: number;
  sizeLabel?: string;
};

/** 从 SessionFile 转为 AttachmentFile */
export function sessionFileToAttachment(f: SessionFile): AttachmentFile {
  return {
    id: f.id,
    filename: f.filename,
    extension: f.extension,
    size: f.size,
  };
}

/** 从 ChatMessage.attachments 项转为 AttachmentFile（无 size 时用 0） */
export function chatAttachmentToDisplay(a: {
  file_id?: string;
  id?: string;
  filename: string;
  size?: number;
  [key: string]: unknown;
}): AttachmentFile {
  const ext = (a.filename || "").split(".").pop() || "";
  return {
    id: a.file_id || a.id || "",
    filename: a.filename || "",
    extension: ext,
    size: typeof a.size === "number" ? a.size : 0,
  };
}

function stableId(prefix: string, index: number, suffix: string): string {
  return `${prefix}-${index}-${suffix}`;
}

function markLatestClarifyAnswered(list: TimelineItem[]): void {
  for (let i = list.length - 1; i >= 0; i--) {
    const item = list[i];
    if (item.kind === "clarify" && item.interactive) {
      list[i] = { ...item, interactive: false };
      return;
    }
  }
}

function toMillis(ts: number | string | undefined | null): number | undefined {
  if (ts === undefined || ts === null) return undefined;
  let value = typeof ts === "string" ? Date.parse(ts) : ts;
  if (Number.isNaN(value)) return undefined;
  if (typeof ts === "number" && value < 10000000000) {
    value *= 1000;
  }
  return value;
}

export function formatDuration(ms: number | undefined | null): string | undefined {
  if (ms === undefined || ms === null || Number.isNaN(ms)) return undefined;
  if (ms < 1000) return `${Math.max(0, Math.round(ms))}ms`;
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest > 0 ? `${minutes}m ${rest}s` : `${minutes}m`;
}

/** 将时间戳格式化为相对时间，如 2天前、刚刚 */
function formatTimeLabel(ts: number | string | undefined): string | undefined {
  if (ts === undefined || ts === null) return undefined;
  let t = typeof ts === "string" ? parseInt(ts, 10) : ts;
  if (Number.isNaN(t)) return undefined;

  // 后端返回的是秒级时间戳（10位数），需要转为毫秒级（13位数）
  if (t < 10000000000) {
    t = t * 1000;
  }

  const now = Date.now();
  const diff = now - t;
  if (diff < 0) return translate("common.justNow");
  if (diff < 60 * 1000) return translate("common.justNow");
  if (diff < 60 * 60 * 1000) {
    return translate("common.relativeTime.minutesAgo", {
      count: Math.floor(diff / (60 * 1000)),
    });
  }
  if (diff < 24 * 60 * 60 * 1000) {
    return translate("common.relativeTime.hoursAgo", {
      count: Math.floor(diff / (60 * 60 * 1000)),
    });
  }
  if (diff < 2 * 24 * 60 * 60 * 1000) return translate("common.dates.yesterday");
  if (diff < 7 * 24 * 60 * 60 * 1000) {
    return translate("common.relativeTime.daysAgo", {
      count: Math.floor(diff / (24 * 60 * 60 * 1000)),
    });
  }
  if (diff < 30 * 24 * 60 * 60 * 1000) {
    return translate("common.relativeTime.weeksAgo", {
      count: Math.floor(diff / (7 * 24 * 60 * 60 * 1000)),
    });
  }
  return undefined;
}

export function getToolTimeLabel(tool: ToolEvent): string | undefined {
  const ts =
    (tool as { timestamp?: number; created_at?: number; ts?: number }).timestamp ??
    (tool as { created_at?: number }).created_at ??
    (tool as { ts?: number }).ts;
  return formatTimeLabel(ts);
}

/**
 * 将 SSE 事件列表归并为时间线展示项（顺序与设计一致）
 */
export function eventsToTimeline(events: SSEEventData[]): TimelineItem[] {
  const list: TimelineItem[] = [];
  let lastStepId: string | null = null;
  let messageIndex = 0;
  let toolIndex = 0;
  let stepIndex = 0;
  let errorIndex = 0;
  let clarifyIndex = 0;
  let lastContextLabel: string | undefined;
  const streamMessages = new Map<string, { listIndex: number; content: string }>();

  for (const ev of events) {
    const visibility = getEventVisibility(ev);
    if (visibility === "internal" || visibility === "debug") {
      if (ev.type !== "debug_item" && ev.type !== "message_delta") {
        continue;
      }
    }

    switch (ev.type) {
      case "message_delta": {
        const deltaData = ev.data as {
          stream_id?: string;
          delta?: string;
          role?: string;
          event_id?: string;
        };
        if (deltaData.role && deltaData.role !== "assistant") break;
        const streamId = deltaData.stream_id || deltaData.event_id;
        if (!streamId || !deltaData.delta) break;
        const existing = streamMessages.get(streamId);
        if (existing) {
          existing.content += deltaData.delta;
          const item = list[existing.listIndex];
          if (item?.kind === "assistant") {
            list[existing.listIndex] = {
              ...item,
              data: { ...item.data, message: existing.content, stream_id: streamId },
            };
          }
        } else {
          const listIndex = list.length;
          streamMessages.set(streamId, { listIndex, content: deltaData.delta });
          list.push({
            kind: "assistant",
            id: stableId("assistant-stream", messageIndex++, streamId),
            data: { role: "assistant", message: deltaData.delta, stream_id: streamId },
          });
        }
        break;
      }
      case "reasoning_delta":
      case "tool_args_delta":
        break;
      case "assistant_notice": {
        const notice = ev.data as { message?: string };
        if (!notice.message) break;
        list.push({
          kind: "assistant",
          id: stableId("assistant", messageIndex++, String(list.length)),
          data: { role: "assistant", message: notice.message },
        });
        break;
      }
      case "debug_item":
        break;
      case "session_status": {
        const status = (ev.data as { status?: string }).status;
        if (status === "cancelled") {
          list.push({
            kind: "assistant",
            id: stableId("system", messageIndex++, `cancelled-${list.length}`),
            data: {
              role: "assistant",
              message: translate("sessionDetail.taskCancelledNotice"),
            },
          });
        } else if (status === "failed") {
          list.push({
            kind: "assistant",
            id: stableId("system", messageIndex++, `failed-${list.length}`),
            data: {
              role: "assistant",
              message: translate("sessionDetail.taskFailedNotice"),
            },
          });
        }
        break;
      }
      case "clarify": {
        const data = ev.data as {
          title?: string | null;
          questions?: ClarifyQuestion[];
          event_id?: string;
        };
        for (let i = list.length - 1; i >= 0; i--) {
          const item = list[i];
          if (item.kind === "clarify" && item.interactive) {
            list[i] = { ...item, interactive: false };
            break;
          }
        }
        list.push({
          kind: "clarify",
          id: stableId("clarify", clarifyIndex++, data.event_id || String(list.length)),
          title: data.title,
          questions: Array.isArray(data.questions) ? data.questions : [],
          interactive: true,
        });
        break;
      }
      case "message": {
        const msg = ev.data as ChatMessage;
        if (msg.role === "user") {
          markLatestClarifyAnswered(list);
          lastStepId = null;
          const anchorEventId = (msg as { event_id?: string }).event_id;
          list.push({
            kind: "user",
            id: stableId("user", messageIndex++, String(list.length)),
            data: msg,
            anchorEventId,
          });
          if (msg.attachments && msg.attachments.length > 0) {
            list.push({
              kind: "attachments",
              id: stableId("att", messageIndex, "user"),
              role: "user",
              files: msg.attachments.map(chatAttachmentToDisplay),
            });
          }
        } else if (msg.role === "assistant") {
          if (msg.message && looksLikePlannerJson(msg.message)) {
            break;
          }
          const streamId = (msg as { stream_id?: string }).stream_id;
          if (streamId && streamMessages.has(streamId)) {
            const existing = streamMessages.get(streamId)!;
            existing.content = msg.message || existing.content;
            const item = list[existing.listIndex];
            if (item?.kind === "assistant") {
              list[existing.listIndex] = {
                ...item,
                data: msg,
              };
            }
            break;
          }
          list.push({
            kind: "assistant",
            id: stableId("assistant", messageIndex++, String(list.length)),
            data: msg,
          });
          if (msg.attachments && msg.attachments.length > 0) {
            list.push({
              kind: "attachments",
              id: stableId("att", messageIndex, "assistant"),
              role: "assistant",
              files: msg.attachments.map(chatAttachmentToDisplay),
            });
          }
        }
        break;
      }
      case "step": {
        const step = ev.data as StepEvent;
        const stepAnchorEventId = (step as { event_id?: string }).event_id;
        lastContextLabel = translate("common.contextStep", { description: step.description });

        // 判断是更新现有 step 还是创建新 step
        // 关键：只有当 lastStepId === step.id 时才是同一个 step 的状态更新
        if (lastStepId !== null && lastStepId === step.id) {
          // 这是同一个 step 的状态更新（running -> completed）
          // 从后往前查找，确保找到最新的（最后一个）匹配的 step
          let existingIdx = -1;
          for (let i = list.length - 1; i >= 0; i--) {
            const item = list[i];
            if (item.kind === "step" && item.data.id === step.id) {
              existingIdx = i;
              break;
            }
          }

          if (existingIdx >= 0) {
            const existing = list[existingIdx];
            if (existing.kind === "step") {
              list[existingIdx] = {
                kind: "step",
                id: existing.id,
                data: step,
                tools: existing.tools,
                anchorEventId: existing.anchorEventId ?? stepAnchorEventId,
              };
            }
          }
        } else {
          // 新的 step (第一次出现或新对话轮次的 step)
          list.push({
            kind: "step",
            id: stableId("step", stepIndex++, step.id + "_" + String(list.length)),
            data: step,
            tools: [],
            anchorEventId: stepAnchorEventId,
          });
        }

        // 更新 lastStepId 跟踪
        // 只要 step 不是 completed/failed 状态，就保持跟踪
        if (step.status === "completed" || step.status === "failed") {
          lastStepId = null;
        } else {
          // running, pending 等其他状态都设置 lastStepId
          lastStepId = step.id;
        }

        break;
      }
      case "subagent": {
        const sub = ev.data as SubAgentEvent;
        const anchor = (sub as { event_id?: string }).event_id;
        const existingIdx = list.findIndex(
          (item) => item.kind === "subagent" && item.data.subagent_id === sub.subagent_id,
        );
        if (existingIdx >= 0) {
          const existing = list[existingIdx];
          if (existing.kind === "subagent") {
            list[existingIdx] = {
              kind: "subagent",
              id: existing.id,
              data: sub,
              anchorEventId: existing.anchorEventId ?? anchor,
            };
          }
        } else {
          list.push({
            kind: "subagent",
            id: stableId("subagent", stepIndex++, sub.subagent_id),
            data: sub,
            anchorEventId: anchor,
          });
        }
        lastContextLabel = translate("common.contextSubtask", { goal: sub.goal });
        break;
      }
      case "tool": {
        const tool = ev.data as ToolEvent;
        const toolCallId = (tool as { tool_call_id?: string }).tool_call_id;
        lastContextLabel = translate("common.contextTool", {
          name: tool.name || tool.function || "",
        });

        if (lastStepId !== null) {
          // 工具属于当前 step，添加到 step 的 tools 中
          // 重要：从后往前查找，确保找到最新的（最后一个）匹配的 step
          let stepIdx = -1;
          for (let i = list.length - 1; i >= 0; i--) {
            const item = list[i];
            if (item.kind === "step" && item.data.id === lastStepId) {
              stepIdx = i;
              break;
            }
          }

          if (stepIdx >= 0) {
            const step = list[stepIdx];
            if (step.kind === "step") {
              if (toolCallId != null) {
                // 检查是否已存在相同 tool_call_id 的工具（更新场景）
                const existingToolIdx = step.tools.findIndex(
                  (t) => (t as { tool_call_id?: string }).tool_call_id === toolCallId,
                );
                if (existingToolIdx >= 0) {
                  // 更新现有工具
                  const newTools = [...step.tools];
                  newTools[existingToolIdx] = tool;
                  list[stepIdx] = { ...step, tools: newTools };
                  break;
                }
              }
              // 添加新工具
              list[stepIdx] = { ...step, tools: [...step.tools, tool] };
            }
          }
        } else {
          // 工具不属于任何 step，作为独立工具添加
          if (toolCallId != null) {
            const last = list[list.length - 1];
            if (
              last?.kind === "tool" &&
              (last.data as { tool_call_id?: string }).tool_call_id === toolCallId
            ) {
              // 更新最后一个独立工具
              list[list.length - 1] = { ...last, data: tool };
              break;
            }
          }
          // 添加新的独立工具
          list.push({
            kind: "tool",
            id: stableId("tool", toolIndex++, (tool.name || "") + (tool.function || "")),
            data: tool,
            timeLabel: getToolTimeLabel(tool),
          });
        }
        break;
      }
      case "title":
      case "plan":
      case "done":
        break;
      case "wait": {
        const data = ev.data as { message?: string; reason?: string; prompt?: string; created_at?: number };
        list.push({
          kind: "wait",
          id: stableId("wait", errorIndex++, String(list.length)),
          message: data.message || data.prompt || data.reason || translate("sessionDetail.waitForInput"),
          timestamp: data.created_at,
        });
        break;
      }
      case "error": {
        // 处理错误事件
        const errorData = ev.data as {
          error?: string;
          created_at?: number;
          event_id?: string;
          [key: string]: unknown;
        };
        if (errorData.error) {
          list.push({
            kind: "error",
            id: stableId("error", errorIndex++, String(list.length)),
            error: errorData.error,
            timestamp: errorData.created_at,
            contextLabel: lastContextLabel,
          });
        }
        break;
      }
      default:
        break;
    }
  }

  return list;
}

export function getTaskObservationSummary(
  events: SSEEventData[],
  sessionStatus?: string,
): TaskObservationSummary {
  let startedAt: number | undefined;
  let endedAt: number | undefined;
  let toolCount = 0;
  let errorCount = 0;
  let waitCount = 0;
  let debugCount = 0;
  const seenTools = new Set<string>();

  for (const ev of events) {
    const createdAt = toMillis((ev.data as { created_at?: number | string }).created_at);
    if (createdAt !== undefined) {
      startedAt = startedAt === undefined ? createdAt : Math.min(startedAt, createdAt);
      endedAt = endedAt === undefined ? createdAt : Math.max(endedAt, createdAt);
    }
    if (ev.type === "tool") {
      const tool = ev.data as ToolEvent;
      const id = tool.tool_call_id || `${tool.name}:${tool.function}:${toolCount}`;
      if (!seenTools.has(id)) {
        seenTools.add(id);
        toolCount += 1;
      }
      if (tool.error) errorCount += 1;
    } else if (ev.type === "error") {
      errorCount += 1;
    } else if (ev.type === "wait") {
      waitCount += 1;
    } else if (ev.type === "debug_item" || ev.type === "reasoning_delta" || ev.type === "tool_args_delta") {
      debugCount += 1;
    }
  }

  const durationEnd =
    sessionStatus === "running" && startedAt !== undefined ? Date.now() : endedAt ?? startedAt;
  const durationMs =
    startedAt !== undefined && durationEnd !== undefined ? Math.max(0, durationEnd - startedAt) : undefined;

  return { startedAt, endedAt, durationMs, toolCount, errorCount, waitCount, debugCount };
}

/**
 * 流式 delta 事件增量 patch，避免每 token 全量 eventsToTimeline。
 * 返回 null 表示需全量重建。
 */
export function patchTimelineForDeltaEvent(
  timeline: TimelineItem[],
  ev: SSEEventData,
): TimelineItem[] | null {
  if (!TRANSIENT_EVENT_TYPES.has(ev.type)) {
    return null;
  }

  if (ev.type === "message_delta") {
    const deltaData = ev.data as {
      stream_id?: string;
      delta?: string;
      role?: string;
      event_id?: string;
    };
    if (deltaData.role && deltaData.role !== "assistant") return timeline;
    const streamId = deltaData.stream_id || deltaData.event_id;
    if (!streamId || !deltaData.delta) return timeline;

    for (let i = timeline.length - 1; i >= 0; i--) {
      const item = timeline[i];
      if (item.kind !== "assistant") continue;
      const itemStreamId = (item.data as { stream_id?: string }).stream_id;
      if (itemStreamId !== streamId) continue;
      const next = [...timeline];
      const currentMessage = (item.data as ChatMessage).message ?? "";
      next[i] = {
        ...item,
        data: {
          ...item.data,
          message: `${currentMessage}${deltaData.delta}`,
          stream_id: streamId,
        },
      };
      return next;
    }

    return [
      ...timeline,
      {
        kind: "assistant" as const,
        id: `assistant-stream-${streamId}`,
        data: { role: "assistant" as const, message: deltaData.delta, stream_id: streamId },
      },
    ];
  }

  if (ev.type === "reasoning_delta") {
    const deltaData = ev.data as { stream_id?: string; delta?: string; event_id?: string };
    const streamId = deltaData.stream_id || deltaData.event_id;
    if (!streamId || !deltaData.delta) return timeline;

    for (let i = timeline.length - 1; i >= 0; i--) {
      const item = timeline[i];
      if (item.kind !== "assistant") continue;
      const itemStreamId = (item.data as { stream_id?: string }).stream_id;
      if (itemStreamId !== streamId) continue;
      const next = [...timeline];
      const currentReasoning = (item.data as { reasoning?: string }).reasoning ?? "";
      next[i] = {
        ...item,
        data: {
          ...item.data,
          reasoning: `${currentReasoning}${deltaData.delta}`,
          stream_id: streamId,
        },
      };
      return next;
    }
    return timeline;
  }

  if (ev.type === "tool_args_delta") {
    const deltaData = ev.data as {
      tool_call_id?: string;
      delta?: string;
      tool_name?: string;
    };
    const toolCallId = deltaData.tool_call_id;
    if (!toolCallId || !deltaData.delta) return timeline;

    for (let i = timeline.length - 1; i >= 0; i--) {
      const item = timeline[i];
      if (item.kind === "tool") {
        const id = (item.data as { tool_call_id?: string }).tool_call_id;
        if (id !== toolCallId) continue;
        const next = [...timeline];
        const currentArgs = (item.data as { function_args?: string }).function_args ?? "";
        next[i] = {
          ...item,
          data: {
            ...item.data,
            function_args: `${currentArgs}${deltaData.delta}`,
          },
        };
        return next;
      }
      if (item.kind === "step") {
        for (let j = item.tools.length - 1; j >= 0; j--) {
          const tool = item.tools[j];
          if ((tool as { tool_call_id?: string }).tool_call_id !== toolCallId) continue;
          const next = [...timeline];
          const stepItem = { ...next[i], tools: [...item.tools] };
          const currentArgs = (tool as { function_args?: string }).function_args ?? "";
          stepItem.tools[j] = {
            ...tool,
            function_args: `${currentArgs}${deltaData.delta}`,
          };
          next[i] = stepItem;
          return next;
        }
      }
    }
    return timeline;
  }

  return timeline;
}

/**
 * 从事件列表中取最新的 plan 步骤（用于底部任务进度面板）
 * 先取最近一次 plan 快照，再合并其后 step 事件的实时状态。
 */
export function getLatestPlanFromEvents(events: SSEEventData[]): PlanStep[] {
  let planIndex = -1;
  let steps: PlanStep[] = [];
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i];
    if (ev.type === "plan") {
      const plan = ev.data as PlanEvent;
      if (plan.steps && Array.isArray(plan.steps)) {
        steps = plan.steps.map((step) => ({ ...step }));
      }
      planIndex = i;
      break;
    }
  }
  if (planIndex < 0 || steps.length === 0) {
    return steps;
  }

  const stepById = new Map(steps.map((step) => [step.id, step]));
  for (let i = planIndex + 1; i < events.length; i++) {
    const ev = events[i];
    if (ev.type !== "step") continue;
    const stepData = ev.data as StepEvent;
    if (!stepData.id) continue;
    const existing = stepById.get(stepData.id);
    if (!existing) continue;
    existing.status = stepData.status;
    if (stepData.description) existing.description = stepData.description;
    if (stepData.started_at !== undefined) existing.started_at = stepData.started_at;
    if (stepData.ended_at !== undefined) existing.ended_at = stepData.ended_at;
    if (stepData.duration_ms !== undefined) existing.duration_ms = stepData.duration_ms;
    if (stepData.error !== undefined) existing.error = stepData.error;
  }
  return steps;
}
