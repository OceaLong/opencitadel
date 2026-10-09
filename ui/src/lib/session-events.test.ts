import { describe, expect, it } from "vitest";

import type { EventMeta, SSEEventData } from "@/lib/api/types";

import { eventsToTimeline, normalizeEvents, reduceSessionStatusEvents } from "./session-events";

function meta(eventId: string, createdAt = 1): EventMeta {
  return {
    event_id: eventId,
    schema_version: 1,
    visibility: "user",
    channel: "ui",
    persist: true,
    created_at: createdAt,
  };
}

describe("formal session event timeline", () => {
  it("renders messages and collapses tool lifecycle events by call id", () => {
    const events: SSEEventData[] = [
      {
        type: "message",
        data: { role: "user", message: "do it", ...meta("1") },
      },
      {
        type: "tool",
        data: {
          tool_call_id: "call-1",
          name: "search_web",
          function: "search_web",
          args: {},
          status: "started",
          ...meta("2"),
        },
      },
      {
        type: "tool",
        data: {
          tool_call_id: "call-1",
          name: "search_web",
          function: "search_web",
          args: {},
          status: "completed",
          content: "result",
          ...meta("3"),
        },
      },
      {
        type: "message",
        data: { role: "assistant", message: "done", ...meta("4") },
      },
    ];

    const timeline = eventsToTimeline(events);

    expect(timeline.map((item) => item.kind)).toEqual(["user", "tool", "assistant"]);
    expect(timeline[1]).toMatchObject({
      kind: "tool",
      data: { tool_call_id: "call-1", status: "completed", content: "result" },
    });
  });

  it("localizes and coalesces identical consecutive Run failures", () => {
    const events: SSEEventData[] = [
      {
        type: "error",
        data: { error: "raw", code: "MODEL_UNAVAILABLE", ...meta("1", 1) },
      },
      {
        type: "error",
        data: { error: "raw", code: "MODEL_UNAVAILABLE", ...meta("2", 2) },
      },
    ];

    const timeline = eventsToTimeline(events);

    expect(timeline).toHaveLength(1);
    expect(timeline[0]).toMatchObject({ kind: "error", repeatCount: 2 });
    if (timeline[0]?.kind === "error") expect(timeline[0].error).not.toBe("raw");
  });
});

describe("formal session status reduction", () => {
  function status(
    value: "running" | "waiting" | "completed" | "cancelled" | "failed",
    eventId: string,
  ): SSEEventData {
    return { type: "session_status", data: { status: value, ...meta(eventId) } };
  }

  it("latches a terminal state until a later Run starts", () => {
    expect(
      reduceSessionStatusEvents([
        status("running", "1"),
        status("failed", "2"),
        status("waiting", "3"),
      ]),
    ).toBe("failed");
    expect(
      reduceSessionStatusEvents([
        status("running", "1"),
        status("failed", "2"),
        status("running", "3"),
        status("completed", "4"),
      ]),
    ).toBe("completed");
  });
});
it("preserves authoritative HTTP Run identity when normalizing public event envelopes", () => {
  const result = normalizeEvents([
    {
      run_id: "real-run",
      event_type: "message",
      payload: { event_id: "opaque", role: "user", message: "hello", persist: true },
    },
  ]);
  expect(result[0].data).toMatchObject({ run_id: "real-run", event_id: "opaque" });
});
