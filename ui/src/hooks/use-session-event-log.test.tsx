// @vitest-environment jsdom

import { act, useEffect } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SSEEventData } from "@/lib/api/types";

import { renderComponent } from "@/test-utils/render";

const mocks = vi.hoisted(() => ({ getSessionEvents: vi.fn() }));

vi.mock("@/lib/api/session", () => ({
  sessionApi: { getSessionEvents: mocks.getSessionEvents },
}));

import { useSessionEventLog } from "./use-session-event-log";

type EventLog = ReturnType<typeof useSessionEventLog>;
let currentLog: EventLog | null = null;

function Harness() {
  const eventLog = useSessionEventLog("session-1");
  useEffect(() => {
    currentLog = eventLog;
    return () => {
      currentLog = null;
    };
  }, [eventLog]);
  return null;
}

function message(eventId: string): SSEEventData {
  return {
    type: "message",
    data: {
      role: "assistant",
      message: eventId,
      event_id: eventId,
      schema_version: 1,
      visibility: "user",
      channel: "ui",
      persist: true,
      created_at: 1,
    },
  };
}

function messageWithoutId(text: string): SSEEventData {
  return {
    type: "message",
    data: {
      role: "assistant",
      message: text,
      schema_version: 1,
      visibility: "user",
      channel: "ui",
      persist: false,
      created_at: 1,
    },
  };
}

describe("useSessionEventLog formal event cursor", () => {
  afterEach(() => {
    mocks.getSessionEvents.mockReset();
    currentLog = null;
    document.body.replaceChildren();
  });

  it("loads ordered history and deduplicates a replayed durable event", async () => {
    mocks.getSessionEvents.mockResolvedValue({
      events: [message("event-1"), message("event-2")],
      prev_cursor: null,
      has_earlier: false,
    });
    const { unmount } = await renderComponent(<Harness />);

    await act(async () => {
      await currentLog?.loadEventsPage();
    });
    await act(async () => {
      currentLog?.appendEvent(message("event-2"));
      currentLog?.appendEvent(message("event-3"));
    });

    expect(currentLog?.events.map((event) => event.data.event_id)).toEqual([
      "event-1",
      "event-2",
      "event-3",
    ]);
    expect(currentLog?.lastEventIdRef.current).toBe("event-3");
    await unmount();
  });

  it("continues reconnect pagination from the latest formal cursor", async () => {
    mocks.getSessionEvents
      .mockResolvedValueOnce({ events: [message("event-2")], next_cursor: "page-2" })
      .mockResolvedValueOnce({ events: [message("event-3")], next_cursor: null });
    const { unmount } = await renderComponent(<Harness />);
    await act(async () => {
      currentLog?.appendEvent(message("event-1"));
      await currentLog?.syncMissingEvents();
    });

    expect(mocks.getSessionEvents).toHaveBeenNthCalledWith(1, "session-1", {
      after: "event-1",
      limit: 500,
    });
    expect(mocks.getSessionEvents).toHaveBeenNthCalledWith(2, "session-1", {
      after: "page-2",
      limit: 500,
    });
    expect(currentLog?.events.map((event) => event.data.event_id)).toEqual([
      "event-1",
      "event-2",
      "event-3",
    ]);
    await unmount();
  });

  it("orders out-of-order event ids by event_id, not arrival", async () => {
    const { unmount } = await renderComponent(<Harness />);
    await act(async () => {
      currentLog?.appendEvent(message("event-3"));
      currentLog?.appendEvent(message("event-1"));
      currentLog?.appendEvent(message("event-2"));
    });

    expect(currentLog?.events.map((event) => event.data.event_id)).toEqual([
      "event-1",
      "event-2",
      "event-3",
    ]);
    // 最新游标取最大 event_id，与到达顺序无关。
    expect(currentLog?.lastEventIdRef.current).toBe("event-3");
    await unmount();
  });

  it("deduplicates a repeated event_id", async () => {
    const { unmount } = await renderComponent(<Harness />);
    let first = false;
    let second = false;
    await act(async () => {
      first = currentLog?.appendEvent(message("event-1")) ?? false;
      second = currentLog?.appendEvent(message("event-1")) ?? false;
    });

    expect(first).toBe(true);
    expect(second).toBe(false);
    expect(currentLog?.events.map((event) => event.data.event_id)).toEqual(["event-1"]);
    await unmount();
  });

  it("keeps events without an event_id in arrival order and dedupes identical ones", async () => {
    const { unmount } = await renderComponent(<Harness />);
    await act(async () => {
      currentLog?.appendEvent(messageWithoutId("alpha"));
      currentLog?.appendEvent(messageWithoutId("beta"));
      // 内容完全一致的无 id 事件按指纹去重。
      currentLog?.appendEvent(messageWithoutId("alpha"));
    });

    const messages = currentLog?.events.map((event) =>
      event.type === "message" ? event.data.message : undefined,
    );
    expect(messages).toEqual(["alpha", "beta"]);
    // 无 id 事件不参与游标推进，也不应导致崩溃。
    expect(currentLog?.lastEventIdRef.current).toBeNull();
    await unmount();
  });
});
