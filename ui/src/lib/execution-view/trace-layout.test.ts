import { expect, test } from "vitest";

import type { StepView } from "@/lib/api/types/execution-view";

import { buildTraceRows, spanWidth, stepInterval } from "./trace-layout";
const step = (id: string, extra: Partial<StepView> = {}): StepView => ({
  step_id: id,
  run_id: "r",
  kind: "tool",
  status: "completed",
  relationship: "direct",
  schema_version: 1,
  projection_revision: 1,
  completeness: { state: "complete", missing_fields: [], missing_intervals: [] },
  ...extra,
});
test("unknown and reversed timing stays unknown; parallel intervals are independent", () => {
  expect(spanWidth(null, 10, 100)).toBeNull();
  expect(spanWidth(10, 60, 100)).toBe(50);
  expect(spanWidth(60, 10, 100)).toBeNull();
  expect(spanWidth(0, 10, 0)).toBeNull();
  const a = step("a", { started_at: "2026-01-01T00:00:00Z", ended_at: "2026-01-01T00:00:10Z" });
  expect(stepInterval(a, "2026-01-01T00:00:05Z")).toEqual({
    start: 1767225600000,
    end: 1767225605000,
    open: false,
  });
  expect(stepInterval(step("b", { status: "running", started_at: a.started_at }), null)).toBeNull();
  expect(
    stepInterval(
      step("b", { status: "completed", started_at: a.started_at }),
      "2026-01-01T00:00:05Z",
    ),
  ).toBeNull();
});
test("paging, incomplete history and confirmed orphan are distinct", () => {
  const items = [step("c", { parent_step_id: "p" })];
  expect(buildTraceRows(items, new Set(), { exhausted: false })[0]).toMatchObject({
    parentMissing: false,
    parentState: "pending",
  });
  expect(
    buildTraceRows(items, new Set(), { exhausted: true, complete: false })[0].parentState,
  ).toBe("incomplete");
  expect(
    buildTraceRows(items, new Set(), { exhausted: true, complete: true })[0].parentMissing,
  ).toBe(true);
});
test("filters retain ancestors and count only loaded hidden descendants", () => {
  const items = [
    step("p", { kind: "phase" }),
    step("a", { parent_step_id: "p", tool_name: "match" }),
    step("b", { parent_step_id: "p", tool_name: "other" }),
  ];
  expect(buildTraceRows(items, new Set(), { tool: "match", exhausted: false })).toMatchObject([
    { step: { step_id: "p" }, filteredDescendantCount: 1, context: true },
    { step: { step_id: "a" }, depth: 1 },
  ]);
});
test("same names stay distinct; only logical identity groups attempts", () => {
  const items = [
    step("a", { tool_name: "same", logical_step_id: "l", attempt_id: "1" }),
    step("b", { tool_name: "same", logical_step_id: "l", attempt_id: "2" }),
    step("c", { tool_name: "same" }),
  ];
  const rows = buildTraceRows(items, new Set(["logical:l"]), {});
  expect(rows.map((r) => r.key)).toEqual(["logical:l", "a", "b", "c"]);
  expect(rows[0].attemptCount).toBe(2);
  expect(buildTraceRows([...items, items[0]], new Set(["logical:l"]), {})).toHaveLength(4);
});
test("cycles produce one warning and deep graphs do not recurse", () => {
  const cycles = buildTraceRows(
    [step("a", { parent_step_id: "b" }), step("b", { parent_step_id: "a" }), step("ok")],
    new Set(["a", "b"]),
    {},
  );
  expect(cycles.filter((r) => r.cycle)).toHaveLength(1);
  expect(cycles.some((r) => r.step.step_id === "ok")).toBe(true);
  const items = Array.from({ length: 10000 }, (_, i) =>
    step(String(i), { parent_step_id: i ? String(i - 1) : null }),
  );
  const rows = buildTraceRows(items, new Set(items.map((s) => s.step_id)), {});
  expect(rows).toHaveLength(10000);
  expect(rows.at(-1)?.depth).toBe(9999);
});
test("cross-parent retries preserve each parent edge and label branch and logical counts", () => {
  const items = [
    step("p"),
    step("q"),
    step("a", { parent_step_id: "p", logical_step_id: "l" }),
    step("b", { parent_step_id: "q", logical_step_id: "l" }),
  ];
  const rows = buildTraceRows(items, new Set(["p", "q", "logical:l:p", "logical:l:q"]), {});
  expect(rows.map((r) => r.key)).toEqual(["p", "logical:l:p", "a", "q", "logical:l:q", "b"]);
  expect(rows.filter((r) => r.summary).map((r) => [r.attemptCount, r.logicalAttemptCount])).toEqual(
    [
      [1, 2],
      [1, 2],
    ],
  );
});
test("visible context ancestors are not counted as hidden descendants", () => {
  const rows = buildTraceRows(
    [
      step("p"),
      step("context", { parent_step_id: "p" }),
      step("match", { parent_step_id: "context", tool_name: "keep" }),
      step("hidden", { parent_step_id: "context" }),
    ],
    new Set(),
    { tool: "keep" },
  );
  expect(rows[0].filteredDescendantCount).toBe(1);
  expect(rows[1].filteredDescendantCount).toBe(1);
});
test("filtered context disclosures can still be collapsed", () => {
  const rows = buildTraceRows(
    [step("p"), step("match", { parent_step_id: "p", tool_name: "keep" })],
    new Set(),
    { tool: "keep", collapsed: new Set(["p"]) },
  );
  expect(rows).toHaveLength(1);
  expect(rows[0].expanded).toBe(false);
});
test("unloaded distinct parents do not merge logical attempts into a false sibling group", () => {
  const rows = buildTraceRows(
    [
      step("a", { parent_step_id: "p", logical_step_id: "l" }),
      step("b", { parent_step_id: "q", logical_step_id: "l" }),
    ],
    new Set(["logical:l:p", "logical:l:q"]),
    { exhausted: false },
  );
  expect(rows.map((r) => r.key)).toEqual(["logical:l:p", "a", "logical:l:q", "b"]);
  expect(rows.filter((r) => r.summary).map((r) => r.attemptCount)).toEqual([1, 1]);
});
test("cycle diagnostics cannot be hidden inside a collapsed retry summary", () => {
  const rows = buildTraceRows(
    [
      step("a", { parent_step_id: "b", logical_step_id: "l" }),
      step("b", { parent_step_id: "a", logical_step_id: "l" }),
    ],
    new Set(),
    {},
  );
  expect(rows.filter((r) => r.cycle)).toHaveLength(1);
});
