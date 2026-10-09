// @vitest-environment jsdom
import { act, useEffect } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

import { type DetailBodyTarget, useExecutionDetailBody } from "./use-execution-detail-body";
afterEach(() => vi.restoreAllMocks());
let current: ReturnType<typeof useExecutionDetailBody>;
const target: DetailBodyTarget = {
  key: "output",
  filename: "output.txt",
  read: async () => ({
    availability: "available",
    content: "old body",
    content_type: "text/plain",
    truncated: false,
    redacted: false,
    at: "cut",
  }),
  download: async () => new Blob(["output"]),
};
function Harness({ visible = true }: { visible?: boolean }) {
  const body = useExecutionDetailBody(
    visible ? { key: "owner", workspaceId: "w", at: "cut" } : null,
    () => {},
  );
  useEffect(() => {
    current = body;
  });
  return <p>{body.items.output?.pages[0]?.content}</p>;
}
test("null authority destroys retained body so returning to the same owner cannot revive it", async () => {
  const r = await renderComponent(<Harness />);
  await act(async () => current.load(target));
  expect(r.container.textContent).toBe("old body");
  await act(async () => r.root.render(<Harness visible={false} />));
  await act(async () => r.root.render(<Harness />));
  expect(r.container.textContent).toBe("");
  await r.unmount();
});
test("a retained callback cannot start reading after the owner unmounts", async () => {
  const r = await renderComponent(<Harness />),
    old = current.load;
  await r.unmount();
  const read = vi.fn(target.read);
  await act(async () => old({ ...target, read }));
  expect(read).not.toHaveBeenCalled();
});

test.each(["source_locator_unavailable", "resource_unavailable"])(
  "actual source download rejects partial bytes and handles %s at the body owner",
  async (reason) => {
    const { executionViewApi } = await import("@/lib/api/execution-view");
    const available = {
      availability: "available" as const,
      content: "private partial",
      content_type: "text/plain",
      truncated: true,
      redacted: false,
      next_cursor: "next",
    };
    const read = vi
      .spyOn(executionViewApi, "readSource")
      .mockResolvedValueOnce(available)
      .mockResolvedValueOnce({
        availability: "unavailable",
        content_type: "text/plain",
        reason,
        content: null,
        truncated: false,
        redacted: false,
      });
    const r = await renderComponent(<Harness />);
    const source = {
      ...target,
      key: "source:citation:original",
      download: (options: Parameters<typeof executionViewApi.downloadSource>[2]) =>
        executionViewApi.downloadSource("citation", {}, options),
    };
    await act(async () => current.load(source));
    await act(async () => current.download(source));
    expect(read).toHaveBeenCalledTimes(2);
    expect(current.items[source.key]?.pages ?? []).toEqual([]);
    if (reason === "source_locator_unavailable") {
      expect(current.denied).toBeFalsy();
      expect(current.items[source.key]?.locatorUnavailable).toBe(true);
      // Explicit fallback is a new target and a new authorized API read.
      read.mockResolvedValueOnce({
        ...available,
        content: "same revision fallback",
        truncated: false,
        next_cursor: null,
      });
      await act(async () =>
        current.load({
          ...source,
          key: "source:citation:page",
          read: (_, options) =>
            executionViewApi.readSource("citation", { locator: "page" }, options),
        }),
      );
      expect(current.items["source:citation:page"]?.pages[0]?.content).toBe(
        "same revision fallback",
      );
    } else expect(current.denied).toBe(true);
    read.mockRestore();
    await r.unmount();
  },
);
