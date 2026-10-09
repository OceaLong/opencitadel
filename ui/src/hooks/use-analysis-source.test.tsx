// @vitest-environment jsdom
import { act } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

import { useAnalysisSource } from "./use-analysis-source";
afterEach(() => vi.useRealTimers());

test("resource-only cost change invalidates all page owners on revalidation", async () => {
  vi.useFakeTimers();
  const invalidate = vi.fn();
  const read = vi.fn(async () => ({ metrics: { score: 4, cost: null } }));
  function Harness() {
    useAnalysisSource({
      value: { metrics: { score: 4, cost: 0.23 } },
      read,
      invalidate,
      workspaceId: "w",
    });
    return null;
  }
  const r = await renderComponent(<Harness />);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30_000);
  });
  expect(read).toHaveBeenCalledWith(expect.objectContaining({ workspaceId: "w" }));
  expect(invalidate).toHaveBeenCalledOnce();
  await r.unmount();
});

test("an old source response cannot invalidate a replacement owner", async () => {
  let resolve!: (v: { metrics: unknown }) => void;
  const read = vi.fn(
    () =>
      new Promise<{ metrics: unknown }>((r) => {
        resolve = r;
      }),
  );
  const invalidate = vi.fn();
  function Harness() {
    useAnalysisSource({ value: { metrics: "old" }, read, invalidate });
    return null;
  }
  const r = await renderComponent(<Harness />);
  await act(async () => {
    window.dispatchEvent(new Event("focus"));
  });
  await r.unmount();
  await act(async () => resolve({ metrics: "changed" }));
  expect(invalidate).not.toHaveBeenCalled();
  expect(read.mock.calls).toHaveLength(1);
});
