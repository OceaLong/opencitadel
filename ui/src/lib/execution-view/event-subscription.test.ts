import { afterEach, expect, test, vi } from "vitest";

import { executionViewApi } from "@/lib/api/execution-view";
import { ApiError } from "@/lib/api/fetch";

import { subscribeExecutionInvalidations } from "./event-subscription";
vi.mock("@/lib/api/execution-view", () => ({
  executionViewApi: { getEvents: vi.fn(), streamEvents: vi.fn() },
}));
afterEach(() => {
  vi.useRealTimers();
  vi.resetAllMocks();
});
test("empty feed reconnects, dedupes overlap and discards retired cursor", async () => {
  vi.useFakeTimers();
  vi.mocked(executionViewApi.getEvents).mockResolvedValue({
    events: [],
    has_earlier: false,
    prev_cursor: null,
    next_cursor: null,
  } as never);
  const event = { event_id: "e", cursor: "opaque" } as never;
  vi.mocked(executionViewApi.streamEvents)
    .mockImplementationOnce(async (_run, receive) => {
      receive(event);
    })
    .mockImplementationOnce(async (_run, receive) => {
      receive(event);
      throw new ApiError(409, "revision_conflict");
    })
    .mockImplementation(() => new Promise(() => {}));
  const invalidate = vi.fn(),
    forbidden = vi.fn();
  const stop = subscribeExecutionInvalidations("r", "team", invalidate, forbidden);
  await vi.advanceTimersByTimeAsync(1000);
  expect(executionViewApi.streamEvents).toHaveBeenNthCalledWith(
    2,
    "r",
    expect.any(Function),
    expect.objectContaining({ lastEventId: "opaque" }),
  );
  await vi.advanceTimersByTimeAsync(2000);
  expect(executionViewApi.getEvents).toHaveBeenCalledTimes(2);
  expect(executionViewApi.streamEvents).toHaveBeenLastCalledWith(
    "r",
    expect.any(Function),
    expect.objectContaining({ lastEventId: undefined }),
  );
  expect(invalidate).toHaveBeenCalledTimes(4); // initial anchor, one event, retired feed, new anchor
  stop();
});
test("permission refresh clears authority and late callbacks cannot resurrect it", async () => {
  vi.useFakeTimers();
  vi.mocked(executionViewApi.getEvents).mockResolvedValue({
    events: [],
    has_earlier: false,
    prev_cursor: null,
    next_cursor: null,
  } as never);
  let late: (() => void) | undefined;
  vi.mocked(executionViewApi.streamEvents).mockImplementation(async (_run, event, options) => {
    options?.onRefresh?.({ code: "permission_denied" });
    late = () => event({ event_id: "late", cursor: "late" } as never);
  });
  const invalidate = vi.fn(),
    forbidden = vi.fn();
  const stop = subscribeExecutionInvalidations("r", "team", invalidate, forbidden);
  await vi.advanceTimersByTimeAsync(10000);
  expect(forbidden).toHaveBeenCalledOnce();
  const count = invalidate.mock.calls.length;
  late?.();
  stop();
  expect(invalidate).toHaveBeenCalledTimes(count);
  expect(executionViewApi.streamEvents).toHaveBeenCalledOnce();
});
