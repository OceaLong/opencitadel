// @vitest-environment jsdom
import { beforeEach, expect, test, vi } from "vitest";

import {
  CONTEXT_TTL_MS,
  NavigationContextError,
  readNavigationContext,
  saveNavigationContext,
} from "./navigation-context";
import { emptySelection } from "./selection";
const owner = "user:workspace";
const ids = Array.from(
  { length: 100_000 },
  (_, i) => `00000000-0000-4000-8000-${i.toString(16).padStart(12, "0")}`,
);
const params = new URLSearchParams({
  filters: JSON.stringify({ start: "2026-09-01T00:00:00Z", end: "2026-09-08T00:00:00Z" }),
  grain: "day",
  timezone: "UTC",
  watermark: "capture",
  cursor: "opaque-page-two",
  scroll: "400",
  baseline: "base",
});
beforeEach(() => {
  sessionStorage.clear();
  vi.useRealTimers();
});
test.each(["explicit", "all_matching"] as const)(
  "100k %s selection restores exactly through bounded nested route",
  (mode) => {
    const selection = {
      ...emptySelection(),
      mode,
      runIds: mode === "explicit" ? ids : [],
      excludedIds: mode === "all_matching" ? ids : [],
      details: [ids[0]],
    };
    const href = saveNavigationContext(
      { path: "/analysis", params, selection, anchor: "run-target" },
      owner,
    );
    expect(href.length).toBeLessThan(150);
    const run = `/runs/run-target?${new URLSearchParams({ analysis_return: href })}`;
    const session = run.replace("/runs/run-target", "/sessions/session");
    expect(session.length).toBeLessThan(250);
    const restored = readNavigationContext(
      "/analysis",
      new URL(href, "https://native.invalid").searchParams,
      owner,
    )!;
    expect(restored.selection).toEqual(selection);
    expect(restored.params.toString()).toBe(params.toString());
    expect(restored.anchor).toBe("run-target");
  },
);
test("UUID packing preserves mixed uppercase and noncanonical strings exactly", () => {
  const selection = {
    ...emptySelection(),
    runIds: [ids[123], ids[123].toUpperCase(), "run-small", "not/a/uuid"],
    details: [],
  };
  const href = saveNavigationContext({ path: "/analysis", params, selection }, owner);
  expect(
    readNavigationContext("/analysis", new URL(href, "https://native.invalid").searchParams, owner)!
      .selection,
  ).toEqual(selection);
});
test("current user/workspace mismatch and capture/path mismatch reject without empty fallback", () => {
  const href = saveNavigationContext(
    { path: "/analysis", params, selection: { ...emptySelection(), runIds: ["r"] } },
    owner,
  );
  const query = new URL(href, "https://native.invalid").searchParams;
  expect(() => readNavigationContext("/analysis", query, "other-user:workspace")).toThrow(
    NavigationContextError,
  );
  expect(() => readNavigationContext("/analysis", query, "user:other-workspace")).toThrow(
    NavigationContextError,
  );
  expect(() => readNavigationContext("/analysis/comparisons/other", query, owner)).toThrow(
    NavigationContextError,
  );
  const key = Object.keys(sessionStorage)[0];
  const envelope = JSON.parse(sessionStorage.getItem(key)!);
  envelope.capture = "other-capture";
  sessionStorage.setItem(key, JSON.stringify(envelope));
  expect(() => readNavigationContext("/analysis", query, owner)).toThrow(NavigationContextError);
});
test("missing, invalid, expired and evicted context are explicit errors", () => {
  expect(() =>
    readNavigationContext(
      "/analysis",
      new URLSearchParams({ context: crypto.randomUUID() }),
      owner,
    ),
  ).toThrow(NavigationContextError);
  expect(() =>
    readNavigationContext("/analysis", new URLSearchParams({ selection: '{"runIds":[]}' }), owner),
  ).toThrow(NavigationContextError);
  vi.useFakeTimers();
  const href = saveNavigationContext(
    { path: "/analysis", params, selection: emptySelection() },
    owner,
  );
  vi.advanceTimersByTime(CONTEXT_TTL_MS + 1);
  expect(() =>
    readNavigationContext("/analysis", new URL(href, "https://native.invalid").searchParams, owner),
  ).toThrow(NavigationContextError);
});
test("bounded cleanup preserves unrelated storage; overcapacity and quota never truncate selection", () => {
  sessionStorage.setItem("unrelated", "keep");
  vi.useFakeTimers();
  let first = "";
  for (let i = 0; i < 12; i++) {
    vi.advanceTimersByTime(1);
    const href = saveNavigationContext(
      { path: "/analysis", params, selection: emptySelection() },
      owner,
    );
    if (i === 0) first = href;
  }
  expect(sessionStorage.getItem("unrelated")).toBe("keep");
  expect(sessionStorage.length).toBeLessThanOrEqual(9);
  expect(() =>
    readNavigationContext(
      "/analysis",
      new URL(first, "https://native.invalid").searchParams,
      owner,
    ),
  ).toThrow(NavigationContextError);
  const selection = {
    ...emptySelection(),
    runIds: Array.from({ length: 100_000 }, () => "x".repeat(100)),
  };
  expect(() => saveNavigationContext({ path: "/analysis", params, selection }, owner)).toThrow(
    NavigationContextError,
  );
  expect(selection.runIds).toHaveLength(100_000);
  const mock = vi.spyOn(window, "sessionStorage", "get").mockReturnValue({
    length: 0,
    key: () => null,
    getItem: () => null,
    removeItem: () => {},
    clear: () => {},
    setItem: () => {
      throw new DOMException("quota", "QuotaExceededError");
    },
  });
  expect(() =>
    saveNavigationContext({ path: "/analysis", params, selection: emptySelection() }, owner),
  ).toThrow(NavigationContextError);
  mock.mockRestore();
});
