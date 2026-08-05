// @vitest-environment jsdom

import { act, useEffect } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SSEEventData } from "@/lib/api/types";
import { reduceSessionStatusEvents } from "@/lib/session-events";

import { renderComponent } from "@/test-utils/render";

const mocks = vi.hoisted(() => ({
  getSessionEvents: vi.fn(),
}));

vi.mock("@/lib/api/session", () => ({
  sessionApi: {
    getSessionEvents: mocks.getSessionEvents,
  },
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

async function renderHookHarness() {
  const { unmount } = await renderComponent(<Harness />);
  return unmount;
}

function statusEvent(
  status: "running" | "failed",
  eventId: string,
): SSEEventData {
  return {
    type: "session_status",
    data: {
      status,
      event_id: eventId,
      schema_version: 2,
      visibility: "user",
      channel: "ui",
      persist: true,
      created_at: Number(eventId),
    },
  };
}

function messageEvent(eventId: string): SSEEventData {
  return {
    type: "message",
    data: {
      role: "assistant",
      message: "higher sequence published first",
      event_id: eventId,
      schema_version: 2,
      visibility: "user",
      channel: "ui",
      persist: true,
      created_at: Number(eventId),
    },
  };
}

describe("useSessionEventLog late persisted events", () => {
  afterEach(() => {
    mocks.getSessionEvents.mockReset();
    currentLog = null;
    document.body.replaceChildren();
  });

  it("recovers and orders a lower terminal published after a higher ordinary event", async () => {
    const running = statusEvent("running", "9");
    const terminal = statusEvent("failed", "10");
    const higherMessage = messageEvent("11");
    mocks.getSessionEvents.mockImplementation(
      async (
        _sessionId: string,
        params: { after?: number; latest?: boolean },
      ) => {
        if (params.after !== undefined) {
          return {
            events: [],
            prev_cursor: null,
            has_earlier: false,
          };
        }
        return {
          events: [terminal, higherMessage],
          prev_cursor: null,
          has_earlier: false,
        };
      },
    );
    const unmount = await renderHookHarness();

    await act(async () => {
      currentLog?.appendEvent(running);
      currentLog?.appendEvent(higherMessage);
    });
    await act(async () => {
      await currentLog?.syncMissingEvents(false);
    });

    const eventIds = currentLog?.events.map(
      (event) => (event.data as { event_id?: string }).event_id,
    );
    expect(eventIds).toEqual(["9", "10", "11"]);
    expect(reduceSessionStatusEvents(currentLog?.events ?? [])).toBe(
      "failed",
    );
    await unmount();
  });

  it("walks reconnect pages back to the pre-live cursor for a delayed terminal", async () => {
    const running = statusEvent("running", "1");
    const terminal = statusEvent("failed", "2");
    const middlePage = Array.from(
      { length: 500 },
      (_, index) => messageEvent(String(index + 3)),
    );
    const latestPage = Array.from(
      { length: 500 },
      (_, index) => messageEvent(String(index + 503)),
    );
    mocks.getSessionEvents.mockImplementation(
      async (
        _sessionId: string,
        params: { before?: number; latest?: boolean },
      ) => {
        if (params.latest) {
          return {
            events: latestPage,
            prev_cursor: 503,
            has_earlier: true,
          };
        }
        if (params.before === 503) {
          return {
            events: middlePage,
            prev_cursor: 3,
            has_earlier: true,
          };
        }
        if (params.before === 3) {
          return {
            events: [running, terminal],
            prev_cursor: 1,
            has_earlier: false,
          };
        }
        throw new Error(`unexpected cursor ${params.before}`);
      },
    );
    const unmount = await renderHookHarness();

    await act(async () => {
      currentLog?.appendEvent(running);
      currentLog?.appendEvent(messageEvent("1002"));
    });
    await act(async () => {
      await currentLog?.syncMissingEvents(false);
    });

    expect(mocks.getSessionEvents).toHaveBeenCalledTimes(3);
    expect(
      currentLog?.events.some(
        (event) => (event.data as { event_id?: string }).event_id === "2",
      ),
    ).toBe(true);
    expect(reduceSessionStatusEvents(currentLog?.events ?? [])).toBe(
      "failed",
    );
    await unmount();
  });
});
