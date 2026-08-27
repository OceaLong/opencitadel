import type { SessionStatus, SSEEventData, SSEEventType } from "@/lib/api/types";

/** 后端返回的原始事件（可能用 event 或 type 表示类型） */
type RawEvent = {
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
  const data = raw.data ?? raw.payload;
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

const TERMINAL_SESSION_STATUSES = new Set<SessionStatus>([
  "waiting",
  "completed",
  "cancelled",
  "failed",
]);

function isTerminalSessionStatus(
  status: SessionStatus | undefined,
): status is "waiting" | "completed" | "cancelled" | "failed" {
  return status !== undefined && TERMINAL_SESSION_STATUSES.has(status);
}

export type SessionStatusReductionState = {
  status?: SessionStatus;
  persistedTerminal?: "waiting" | "completed" | "cancelled" | "failed";
  lastPersistedSeq?: number;
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
    const parsedSeq = persisted ? Number(data.event_id) : Number.NaN;
    const seq = Number.isInteger(parsedSeq) && parsedSeq > 0 ? parsedSeq : undefined;
    if (
      seq !== undefined &&
      state.lastPersistedSeq !== undefined &&
      seq <= state.lastPersistedSeq
    ) {
      continue;
    }
    if (seq !== undefined) state.lastPersistedSeq = seq;

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
