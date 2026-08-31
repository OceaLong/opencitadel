// @vitest-environment jsdom

import { act, useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SSEEventData } from "@/lib/api/types";

import { renderComponent } from "@/test-utils/render";

const mocks = vi.hoisted(() => ({
  chat: vi.fn(),
  translate: vi.fn((key: string) => key),
}));

vi.mock("next-intl", () => ({
  useTranslations: () => mocks.translate,
}));

vi.mock("@/lib/api/session", () => ({
  sessionApi: {
    chat: mocks.chat,
  },
}));

import { useSessionStreams } from "./use-session-streams";

type VolatileCallbacks = {
  appendEvent: (event: SSEEventData) => boolean;
  onSessionMissing: (error: unknown) => void;
  applySessionPatch: () => void;
  setError: () => void;
  onReconnect: () => Promise<void>;
};

const lastEventIdRef = { current: "10" as string | null };

function makeCallbacks(): VolatileCallbacks {
  return {
    appendEvent: () => true,
    onSessionMissing: () => undefined,
    applySessionPatch: () => undefined,
    setError: () => undefined,
    onReconnect: async () => undefined,
  };
}

function Harness({ callbacks }: { callbacks: VolatileCallbacks }) {
  const streams = useSessionStreams({
    sessionId: "session-1",
    sessionStatus: "running",
    appendEvent: callbacks.appendEvent,
    onSessionMissing: callbacks.onSessionMissing,
    applySessionPatch: callbacks.applySessionPatch,
    setError: callbacks.setError,
    lastEventIdRef,
    initialEventsLoaded: true,
    onReconnect: callbacks.onReconnect,
  });
  useEffect(() => {
    currentStreams = streams;
    return () => {
      currentStreams = null;
    };
  }, [streams]);
  return null;
}

let currentStreams: ReturnType<typeof useSessionStreams> | null = null;

describe("useSessionStreams empty stream lifecycle", () => {
  const cleanups: Array<ReturnType<typeof vi.fn>> = [];

  beforeEach(() => {
    vi.useFakeTimers();
    cleanups.length = 0;
    mocks.chat.mockImplementation(() => {
      const cleanup = vi.fn();
      cleanups.push(cleanup);
      return cleanup;
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
    currentStreams = null;
    document.body.replaceChildren();
  });

  it("keeps one connection when only callback identities change", async () => {
    const firstCallbacks = makeCallbacks();
    const { root, unmount } = await renderComponent(<Harness callbacks={firstCallbacks} />);

    await act(async () => {
      vi.advanceTimersByTime(0);
    });
    expect(mocks.chat).toHaveBeenCalledTimes(1);
    expect(cleanups[0]).not.toHaveBeenCalled();

    await act(async () => {
      root.render(<Harness callbacks={makeCallbacks()} />);
    });
    await act(async () => {
      vi.advanceTimersByTime(0);
    });

    expect(mocks.chat).toHaveBeenCalledTimes(1);
    expect(cleanups[0]).not.toHaveBeenCalled();

    await unmount();
    expect(cleanups[0]).toHaveBeenCalledTimes(1);
  });

  it("resumes a durable event stream after an approval command", async () => {
    const callbacks = makeCallbacks();
    const { unmount } = await renderComponent(<WaitingHarness callbacks={callbacks} />);

    await act(async () => {
      vi.advanceTimersByTime(0);
    });
    expect(mocks.chat).not.toHaveBeenCalled();

    await act(async () => {
      currentStreams?.resumeAfterExternalCommand();
    });

    expect(mocks.chat).toHaveBeenCalledTimes(1);
    expect(mocks.chat.mock.calls[0][1]).toEqual({ event_id: "10" });
    await unmount();
  });
});

function WaitingHarness({ callbacks }: { callbacks: VolatileCallbacks }) {
  const streams = useSessionStreams({
    sessionId: "session-1",
    sessionStatus: "waiting",
    appendEvent: callbacks.appendEvent,
    onSessionMissing: callbacks.onSessionMissing,
    applySessionPatch: callbacks.applySessionPatch,
    setError: callbacks.setError,
    lastEventIdRef,
    initialEventsLoaded: true,
    onReconnect: callbacks.onReconnect,
  });
  useEffect(() => {
    currentStreams = streams;
    return () => {
      currentStreams = null;
    };
  }, [streams]);
  return null;
}
