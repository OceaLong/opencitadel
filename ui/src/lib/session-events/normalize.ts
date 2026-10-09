import type { SessionStatus, SSEEventData, SSEEventType } from "@/lib/api/types";

/** 后端返回的原始事件（可能用 event 或 type 表示类型） */
type RawEvent = {
  run_id?: string;
  event?: string;
  type?: string;
  data?: unknown;
  event_type?: string;
  payload?: unknown;
};

/**
 * 将后端单条事件转为前端 SSEEventData（统一 type + data）
 */
export function normalizeEvent(raw: RawEvent): SSEEventData | null {
  const type = (raw.type ?? raw.event ?? raw.event_type) as SSEEventType | undefined;
  const payload = raw.data ?? raw.payload;
  const data =
    raw.run_id && payload && typeof payload === "object"
      ? { ...payload, run_id: raw.run_id }
      : payload;
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

const TERMINAL_SESSION_STATUSES = new Set<SessionStatus>(["completed", "cancelled", "failed"]);

function isTerminalSessionStatus(
  status: SessionStatus | undefined,
): status is "completed" | "cancelled" | "failed" {
  return status !== undefined && TERMINAL_SESSION_STATUSES.has(status);
}

export type SessionStatusReductionState = {
  status?: SessionStatus;
  persistedTerminal?: "completed" | "cancelled" | "failed";
};

export function reduceSessionStatusState(
  events: SSEEventData[],
  initialState: SessionStatusReductionState = {},
): SessionStatusReductionState {
  const state = { ...initialState };
  if (!state.persistedTerminal && isTerminalSessionStatus(state.status)) {
    state.persistedTerminal = state.status;
  }

  for (const event of events) {
    if (event.type !== "session_status") continue;
    const data = event.data as {
      event_id?: string;
      status?: SessionStatus;
      persist?: boolean;
    };
    const incoming = data.status;
    if (!incoming) continue;

    const persisted = data.persist !== false;
    // The event store supplies persisted order. Public IDs are opaque, including
    // strings that happen to look numeric. A session may contain successive Runs.
    if (incoming === "running") {
      state.status = incoming;
      if (persisted) state.persistedTerminal = undefined;
      continue;
    }
    if (state.persistedTerminal) continue;
    if (isTerminalSessionStatus(incoming) && persisted) {
      state.persistedTerminal = incoming;
    }
    state.status = incoming;
  }

  return state;
}

export function reduceSessionStatusEvents(
  events: SSEEventData[],
  initialStatus?: SessionStatus,
): SessionStatus | undefined {
  return reduceSessionStatusState(events, { status: initialStatus }).status;
}
