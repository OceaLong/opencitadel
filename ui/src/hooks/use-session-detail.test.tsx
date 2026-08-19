// @vitest-environment jsdom

import { act, useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

const mocks = vi.hoisted(() => ({
  getSessionDetail: vi.fn(),
  getSessionFiles: vi.fn(),
  listCheckpoints: vi.fn(),
  getSessionEvents: vi.fn(),
  clearUnreadMessageCount: vi.fn(),
  resetStreams: vi.fn(),
  markSessionMissing: vi.fn(),
  translate: vi.fn((key: string) => key),
}));

vi.mock("next-intl", () => ({
  useTranslations: () => mocks.translate,
}));

vi.mock("@/lib/api/session", () => ({
  sessionApi: {
    getSessionDetail: mocks.getSessionDetail,
    getSessionFiles: mocks.getSessionFiles,
    listCheckpoints: mocks.listCheckpoints,
    getSessionEvents: mocks.getSessionEvents,
    clearUnreadMessageCount: mocks.clearUnreadMessageCount,
  },
}));

vi.mock("@/hooks/use-session-streams", () => ({
  useSessionStreams: () => ({
    streaming: false,
    streamStatus: "idle",
    streamError: null,
    sendMessage: vi.fn(async () => undefined),
    resetStreams: mocks.resetStreams,
    markSessionMissing: mocks.markSessionMissing,
    enableDebugStream: vi.fn(),
  }),
}));

import { useSessionDetail, type UseSessionDetailResult } from "./use-session-detail";

let currentDetail: UseSessionDetailResult | null = null;

function event(eventId: string, type: "message" | "debug_item") {
  return {
    event: type,
    data: {
      event_id: eventId,
      schema_version: 2,
      visibility: type === "debug_item" ? "debug" : "user",
      channel: type === "debug_item" ? "debug" : "ui",
      persist: true,
      created_at: Number(eventId),
      ...(type === "message"
        ? { role: "assistant", message: `message-${eventId}` }
        : { item_type: "planner_output", payload: { title: "plan" } }),
    },
  };
}

function Harness() {
  const detail = useSessionDetail("session-1", true);
  useEffect(() => {
    currentDetail = detail;
    return () => {
      currentDetail = null;
    };
  }, [detail]);
  return null;
}

async function settle() {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

describe("useSessionDetail debug event reconciliation", () => {
  beforeEach(() => {
    currentDetail = null;
    mocks.getSessionDetail.mockResolvedValue({
      session_id: "session-1",
      title: "Debug session",
      status: "completed",
      unread_message_count: 0,
      mode: "agent",
      thinking_enabled: false,
      resource_bindings: [],
    });
    mocks.getSessionFiles.mockResolvedValue([]);
    mocks.listCheckpoints.mockResolvedValue({ checkpoints: [] });
    mocks.getSessionEvents.mockImplementation(
      async (_sessionId: string, params: { include_debug?: boolean }) => ({
        events: params.include_debug
          ? [event("2", "message"), event("3", "debug_item")]
          : [event("1", "message"), event("2", "message")],
        prev_cursor: null,
        has_earlier: false,
      }),
    );
  });

  afterEach(() => {
    vi.clearAllMocks();
    currentDetail = null;
    document.body.replaceChildren();
  });

  it("keeps loaded history when debug events are requested", async () => {
    const { unmount } = await renderComponent(<Harness />);
    await settle();

    expect(
      currentDetail?.events.map((item) => (item.data as { event_id?: string }).event_id),
    ).toEqual(["1", "2"]);

    await act(async () => {
      await currentDetail?.refetchEventsWithDebug();
    });

    expect(
      currentDetail?.events.map((item) => (item.data as { event_id?: string }).event_id),
    ).toEqual(["1", "2", "3"]);

    await unmount();
  });
});
