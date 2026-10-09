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

function Harness({
  callbacks,
  status = "running",
}: {
  callbacks: VolatileCallbacks;
  status?: "running" | "waiting";
}) {
  const streams = useSessionStreams({
    sessionId: "session-1",
    sessionStatus: status,
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
    expect(mocks.chat).toHaveBeenCalledTimes(1);

    await act(async () => {
      currentStreams?.resumeAfterExternalCommand();
    });

    expect(mocks.chat).toHaveBeenCalledTimes(2);
    expect(cleanups[0]).toHaveBeenCalledOnce();
    expect(mocks.chat.mock.calls[1][1]).toEqual({ event_id: "10" });
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

it("maintains a waiting subscription and accepts external approval resume", async () => {
  vi.useFakeTimers();
  const cleanup = vi.fn();
  mocks.chat.mockReturnValue(cleanup);
  const patch = vi.fn();
  const callbacks = { ...makeCallbacks(), applySessionPatch: patch };
  const { unmount } = await renderComponent(<Harness callbacks={callbacks} status="waiting" />);
  await act(async () => {
    vi.advanceTimersByTime(0);
  });
  expect(mocks.chat).toHaveBeenCalledTimes(1);
  const onEvent = mocks.chat.mock.calls[0][2] as (event: SSEEventData) => void;
  await act(async () => {
    onEvent({
      type: "session_status",
      data: { status: "running", event_id: "opaque-resumed", persist: true },
    } as SSEEventData);
  });
  expect(patch).toHaveBeenLastCalledWith({ status: "running" });
  expect(currentStreams?.streaming).toBe(true);
  await unmount();
  vi.useRealTimers();
  vi.clearAllMocks();
});
it("new send invalidates current-run authority until its persisted stream establishes the run", async () => {
  mocks.chat.mockReturnValue(() => {});
  const result = await renderComponent(<Harness callbacks={makeCallbacks()} status="waiting" />);
  await act(async () => currentStreams!.sendMessage("new turn", []));
  expect(mocks.chat.mock.calls.at(-1)?.[1].request_id).toBeTruthy();
  expect(currentStreams!.admissionPending).toBe(true);
  const onEvent = mocks.chat.mock.calls.at(-1)![2];
  await act(async () =>
    onEvent({
      type: "message",
      data: { role: "user", message: "new turn", run_id: "R2", event_id: "created", persist: true },
    }),
  );
  expect(currentStreams!.admissionPending).toBe(false);
  await result.unmount();
});

it("retry resume stops generation only for the accepted current persisted waiting fact", async () => {
  vi.useFakeTimers();
  mocks.chat.mockReturnValue(() => {});
  const accept = vi.fn(() => true);
  const result = await renderComponent(
    <Harness callbacks={{ ...makeCallbacks(), appendEvent: accept }} />,
  );
  await act(async () => vi.advanceTimersByTime(0));
  const emit = async (type: string, data: Record<string, unknown>) =>
    act(async () => mocks.chat.mock.calls.at(-1)![2]({ type, data }));
  await emit("error", { retryable: true, run_id: "current", persist: true });
  await emit("session_status", { status: "running", run_id: "current", persist: true });
  expect(currentStreams!.streaming).toBe(true);
  await emit("approval", { run_id: "old", persist: true });
  expect(currentStreams!.streaming).toBe(true);
  await emit("approval", { run_id: "current", persist: false });
  expect(currentStreams!.streaming).toBe(true);
  accept.mockReturnValueOnce(false);
  await emit("approval", { run_id: "current", persist: true });
  expect(currentStreams!.streaming).toBe(true);
  await emit("approval", { run_id: "current", persist: true });
  expect(currentStreams!.streaming).toBe(false);
  await emit("session_status", { status: "running", run_id: "current", persist: true });
  expect(currentStreams!.streaming).toBe(true);
  await result.unmount();
  vi.useRealTimers();
  vi.clearAllMocks();
});
