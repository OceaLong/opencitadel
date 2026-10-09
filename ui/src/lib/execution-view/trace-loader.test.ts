import { expect, test } from "vitest";

import { executionViewApi } from "@/lib/api/execution-view";
import type { StepView, StepViewPage, ViewPage } from "@/lib/api/types/execution-view";

import { loadTracePages } from "./trace-loader";
const s = (id: string) => ({ step_id: id, run_id: "r", projection_revision: 2 }) as StepView;
const page = {
  at: "opaque+/",
  revision: 2,
  hidden_count: 0,
  steps: [s("a")],
  next_cursor: "cursor",
  run: {
    run_id: "r",
    completeness: { state: "complete", missing_fields: [], missing_intervals: [] },
  },
} as unknown as ViewPage;
test("publishes first page before waiting and never mixes a changed cut", async () => {
  const seen: string[][] = [];
  const api = {
    ...executionViewApi,
    listSteps: async (_run: string, q: unknown) => {
      expect(q).toEqual({ at: "opaque+/", revision: 2, cursor: "cursor" });
      return {
        at: "other",
        revision: 2,
        hidden_count: 0,
        items: [s("bad")],
        next_cursor: null,
        completeness: { state: "complete", missing_fields: [], missing_intervals: [] },
      } as StepViewPage;
    },
  };
  await expect(
    loadTracePages(page, null, {}, (state) => seen.push(state.steps.map((s) => s.step_id)), api),
  ).rejects.toMatchObject({ code: 409 });
  expect(seen).toEqual([["a"]]);
});
test("deduplicates exact attempts, accumulates completeness and rejects cursor cycles", async () => {
  const seen: { ids: string[]; exhausted: boolean; complete: boolean }[] = [];
  let n = 0;
  const api = {
    ...executionViewApi,
    listSteps: async () =>
      ({
        at: page.at,
        revision: 2,
        hidden_count: 0,
        items: [s("a"), s("b")],
        next_cursor: ++n === 1 ? "tail" : null,
        completeness: { state: n === 1 ? "partial" : "complete" },
      }) as StepViewPage,
  };
  await loadTracePages(
    page,
    null,
    {},
    (x) =>
      seen.push({
        ids: x.steps.map((s) => s.step_id),
        exhausted: x.exhausted,
        complete: x.complete,
      }),
    api,
  );
  expect(seen.at(-1)).toEqual({ ids: ["a", "b"], exhausted: true, complete: false });
  await expect(
    loadTracePages(page, null, {}, () => {}, {
      ...api,
      listSteps: async () =>
        ({
          at: page.at,
          revision: 2,
          hidden_count: 0,
          items: [],
          next_cursor: "cursor",
          completeness: { state: "complete", missing_fields: [], missing_intervals: [] },
        }) as StepViewPage,
    }),
  ).rejects.toMatchObject({ code: 409 });
});
test("aborted continuations cannot publish and point reads walk selected ancestors at exact cut", async () => {
  const abort = new AbortController();
  const ids: string[][] = [];
  const api = {
    ...executionViewApi,
    listSteps: async () => {
      abort.abort();
      return {
        at: page.at,
        revision: 2,
        hidden_count: 0,
        items: [s("bad")],
        next_cursor: null,
        completeness: { state: "complete", missing_fields: [], missing_intervals: [] },
      } as StepViewPage;
    },
  };
  await expect(
    loadTracePages(
      page,
      null,
      { signal: abort.signal },
      (x) => ids.push(x.steps.map((s) => s.step_id)),
      api,
    ),
  ).rejects.toMatchObject({ name: "AbortError" });
  expect(ids).toEqual([["a"]]);
});
test("deep selection retrieves ancestors using the resolved cut", async () => {
  const seen: string[][] = [];
  const selected = {
    ...s("selected"),
    at: page.at,
    parent_step_id: "parent",
  } as import("@/lib/api/types/execution-view").StepDetail;
  const api = {
    ...executionViewApi,
    getStep: async (run: string, id: string, q: unknown) => {
      expect([run, id, q]).toEqual(["r", "parent", { at: page.at }]);
      return { ...s("parent"), at: page.at, parent_step_id: null } as typeof selected;
    },
  };
  await loadTracePages(
    { ...page, next_cursor: null },
    selected,
    {},
    (x) => seen.push(x.steps.map((s) => s.step_id)),
    api,
  );
  expect(seen.at(-1)).toEqual(["a", "selected", "parent"]);
});
test("200-row initial cut progressively exhausts 10000 rows", async () => {
  const counts: number[] = [];
  let offset = 200;
  await loadTracePages(
    { ...page, steps: Array.from({ length: 200 }, (_, i) => s(String(i))) },
    null,
    {},
    (x) => counts.push(x.steps.length),
    {
      ...executionViewApi,
      listSteps: async () => {
        const items = Array.from({ length: 200 }, (_, i) => s(String(offset + i)));
        offset += 200;
        return {
          at: page.at!,
          revision: 2,
          hidden_count: 0,
          items,
          next_cursor: offset === 10000 ? null : String(offset),
          completeness: { state: "complete", missing_fields: [], missing_intervals: [] },
        };
      },
    },
  );
  expect(counts[0]).toBe(200);
  expect(counts.at(-1)).toBe(10000);
  expect(counts).toHaveLength(50);
});
