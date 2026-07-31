import { describe, expect, it } from "vitest";

import type { EventMeta, SSEEventData } from "@/lib/api/types";

import * as sessionEvents from "./session-events";

const { eventsToTimeline, extractSessionErrors } = sessionEvents;

function eventMeta(createdAt: number, eventId?: string): EventMeta {
  return {
    event_id: eventId,
    schema_version: 2,
    visibility: "user",
    channel: "ui",
    persist: true,
    created_at: createdAt,
  };
}

describe("eventsToTimeline error i18n", () => {
  it("prefers localized quota message when error code is present", () => {
    const events: SSEEventData[] = [
      {
        type: "error",
        data: {
          error:
            "Error code: 403 - {'error': {'code': 'insufficient_quota', 'message': 'The free quota has been exhausted.'}}",
          code: "MODEL_QUOTA_EXCEEDED",
          ...eventMeta(1),
        },
      },
    ];

    const timeline = eventsToTimeline(events);
    const errorItem = timeline.find((item) => item.kind === "error");

    expect(errorItem?.kind).toBe("error");
    if (errorItem?.kind === "error") {
      expect(errorItem.error).not.toContain("insufficient_quota");
      expect(errorItem.error.length).toBeGreaterThan(0);
    }
  });

  it("falls back to raw error text when code is missing", () => {
    const events: SSEEventData[] = [
      {
        type: "error",
        data: {
          error: "plain backend error",
          ...eventMeta(1),
        },
      },
    ];

    const timeline = eventsToTimeline(events);
    const errorItem = timeline.find((item) => item.kind === "error");

    expect(errorItem?.kind).toBe("error");
    if (errorItem?.kind === "error") {
      expect(errorItem.error).toBe("plain backend error");
    }
  });

  it("merges consecutive identical localized errors in timeline", () => {
    const events: SSEEventData[] = [
      {
        type: "error",
        data: { error: "raw", code: "MODEL_UNAVAILABLE", ...eventMeta(1) },
      },
      {
        type: "error",
        data: { error: "raw", code: "MODEL_UNAVAILABLE", ...eventMeta(2) },
      },
      {
        type: "error",
        data: { error: "raw", code: "MODEL_UNAVAILABLE", ...eventMeta(3) },
      },
    ];

    const timeline = eventsToTimeline(events);
    const errorItems = timeline.filter((item) => item.kind === "error");

    expect(errorItems).toHaveLength(1);
    if (errorItems[0]?.kind === "error") {
      expect(errorItems[0].repeatCount).toBe(3);
    }
  });

  it("merges consecutive identical errors in debug extractSessionErrors", () => {
    const events: SSEEventData[] = [
      {
        type: "error",
        data: { error: "raw", code: "MODEL_INVALID_REQUEST", ...eventMeta(1, "1") },
      },
      {
        type: "error",
        data: { error: "raw", code: "MODEL_INVALID_REQUEST", ...eventMeta(2, "2") },
      },
    ];

    const errors = extractSessionErrors(events);
    expect(errors).toHaveLength(1);
    expect(errors[0]?.repeatCount).toBe(2);
  });

  it("localizes assistant_notice via i18n_key", () => {
    const events: SSEEventData[] = [
      {
        type: "assistant_notice",
        data: {
          message: "",
          i18n_key: "sessionDetail.modelFallbackNotice",
          i18n_params: { modelName: "qwen3.6-35b-a3b" },
          ...eventMeta(1),
        },
      },
    ];

    const timeline = eventsToTimeline(events);
    const assistantItem = timeline.find((item) => item.kind === "assistant");

    expect(assistantItem?.kind).toBe("assistant");
    if (assistantItem?.kind === "assistant") {
      expect(assistantItem.data.message).toContain("qwen3.6-35b-a3b");
      expect(assistantItem.data.message).not.toBe("sessionDetail.modelFallbackNotice");
    }
  });

  it("localizes model fallback notice in Chinese when locale is zh", () => {
    const events: SSEEventData[] = [
      {
        type: "assistant_notice",
        data: {
          message: "Current model quota is exhausted. Switched to qwen3.7-max; the task will continue.",
          i18n_key: "sessionDetail.modelFallbackNotice",
          i18n_params: { modelName: "qwen3.7-max" },
          ...eventMeta(1),
        },
      },
    ];

    const timeline = eventsToTimeline(events, "zh");
    const assistantItem = timeline.find((item) => item.kind === "assistant");

    expect(assistantItem?.kind).toBe("assistant");
    if (assistantItem?.kind === "assistant") {
      expect(assistantItem.data.message).toContain("qwen3.7-max");
      expect(assistantItem.data.message).toContain("配额");
      expect(assistantItem.data.message).not.toContain("quota is exhausted");
    }
  });

  it("localizes model fallback notice in English when locale is en", () => {
    const events: SSEEventData[] = [
      {
        type: "assistant_notice",
        data: {
          message: "当前模型配额已耗尽，已自动切换至 qwen3.7-max，任务继续执行。",
          i18n_key: "sessionDetail.modelFallbackNotice",
          i18n_params: { modelName: "qwen3.7-max" },
          ...eventMeta(1),
        },
      },
    ];

    const timeline = eventsToTimeline(events, "en");
    const assistantItem = timeline.find((item) => item.kind === "assistant");

    expect(assistantItem?.kind).toBe("assistant");
    if (assistantItem?.kind === "assistant") {
      expect(assistantItem.data.message).toContain("qwen3.7-max");
      expect(assistantItem.data.message).toContain("quota is exhausted");
      expect(assistantItem.data.message).not.toContain("配额");
    }
  });

  it("falls back to message when i18n_key is missing from bundle", () => {
    const events: SSEEventData[] = [
      {
        type: "assistant_notice",
        data: {
          message: "Fallback notice for qwen3.6-35b-a3b",
          i18n_key: "sessionDetail.unknownMissingKey",
          i18n_params: { modelName: "qwen3.6-35b-a3b" },
          ...eventMeta(1),
        },
      },
    ];

    const timeline = eventsToTimeline(events);
    const assistantItem = timeline.find((item) => item.kind === "assistant");

    expect(assistantItem?.kind).toBe("assistant");
    if (assistantItem?.kind === "assistant") {
      expect(assistantItem.data.message).toBe("Fallback notice for qwen3.6-35b-a3b");
    }
  });
});

describe("session status reducer", () => {
  function statusEvent(
    status: "running" | "waiting" | "completed" | "cancelled" | "failed",
    eventId: string,
    persist = true,
  ): SSEEventData {
    return {
      type: "session_status",
      data: { status, ...eventMeta(Number(eventId), eventId), persist },
    };
  }

  it("retains the first persisted terminal state in a run", () => {
    const reducer = (
      sessionEvents as typeof sessionEvents & {
        reduceSessionStatusEvents?: (events: SSEEventData[]) => string | undefined;
      }
    ).reduceSessionStatusEvents;

    expect(reducer).toBeTypeOf("function");
    expect(
      reducer?.([
        statusEvent("running", "1"),
        statusEvent("failed", "2"),
        statusEvent("completed", "3"),
      ]),
    ).toBe("failed");
  });

  it("allows a later running event to start a new run", () => {
    const reducer = (
      sessionEvents as typeof sessionEvents & {
        reduceSessionStatusEvents?: (events: SSEEventData[]) => string | undefined;
      }
    ).reduceSessionStatusEvents;

    expect(
      reducer?.([
        statusEvent("running", "1"),
        statusEvent("failed", "2"),
        statusEvent("running", "3"),
        statusEvent("completed", "4"),
      ]),
    ).toBe("completed");
  });

  it("ignores a late nonterminal status after the first persisted terminal", () => {
    expect(
      sessionEvents.reduceSessionStatusEvents([
        statusEvent("running", "1"),
        statusEvent("failed", "2"),
        statusEvent("waiting", "3"),
      ]),
    ).toBe("failed");
  });

  it("latches waiting as the terminal outcome of a run epoch", () => {
    expect(
      sessionEvents.reduceSessionStatusEvents([
        statusEvent("running", "1"),
        statusEvent("waiting", "2"),
        statusEvent("completed", "3"),
      ]),
    ).toBe("waiting");
  });

  it("allows a new running epoch after waiting", () => {
    expect(
      sessionEvents.reduceSessionStatusEvents([
        statusEvent("running", "1"),
        statusEvent("waiting", "2"),
        statusEvent("running", "3"),
        statusEvent("completed", "4"),
      ]),
    ).toBe("completed");
  });

  it("ignores a reconnect replay of an older running event", () => {
    expect(
      sessionEvents.reduceSessionStatusEvents([
        statusEvent("running", "10"),
        statusEvent("failed", "11"),
        statusEvent("running", "10"),
      ]),
    ).toBe("failed");
  });

  it("keeps sequence state across one-event stream reductions", () => {
    const first = sessionEvents.reduceSessionStatusState([
      statusEvent("running", "10"),
      statusEvent("failed", "11"),
    ]);
    const replayed = sessionEvents.reduceSessionStatusState(
      [statusEvent("running", "10")],
      first,
    );

    expect(replayed.status).toBe("failed");
    expect(replayed.lastPersistedSeq).toBe(11);
  });

  it("does not latch a transient terminal event", () => {
    const reducer = (
      sessionEvents as typeof sessionEvents & {
        reduceSessionStatusEvents?: (events: SSEEventData[]) => string | undefined;
      }
    ).reduceSessionStatusEvents;

    expect(
      reducer?.([
        statusEvent("running", "1"),
        statusEvent("failed", "2", false),
        statusEvent("completed", "3"),
      ]),
    ).toBe("completed");
  });
});
