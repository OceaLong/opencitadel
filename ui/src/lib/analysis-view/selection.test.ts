import { expect, test } from "vitest";

import { analysisReturn, emptySelection, toggleDetail, toggleRun } from "./selection";
test("all matching retains exclusions while paging without enumerating cohort", () => {
  let s: ReturnType<typeof emptySelection> = { ...emptySelection(), mode: "all_matching" };
  s = toggleRun(s, "page-one", false);
  s = toggleRun(s, "page-two", false);
  expect(s.runIds).toEqual([]);
  expect(s.excludedIds).toEqual(["page-one", "page-two"]);
  s = toggleRun(s, "page-one", true);
  expect(s.excludedIds).toEqual(["page-two"]);
});
test("at most five detail runs independently of cohort selection", () => {
  let s = emptySelection();
  for (let i = 0; i < 6; i++) s = toggleDetail(s, String(i));
  expect(s.details).toEqual(["0", "1", "2", "3", "4"]);
});
test("return destination rejects external or protocol-relative origins", () => {
  expect(analysisReturn("https://evil.test/analysis")).toBeNull();
  expect(analysisReturn("//evil.test/analysis")).toBeNull();
  expect(analysisReturn("/analysis?tool=a#row")).toBe("/analysis?tool=a#row");
  expect(analysisReturn("/sessions/x")).toBeNull();
});
test("a detail owner is always a selected member and deselection removes its body eligibility", () => {
  let selection = toggleDetail(emptySelection(), "run");
  expect(selection.runIds).toEqual(["run"]);
  selection = toggleRun(selection, "run", false);
  expect(selection.details).toEqual([]);
  selection = toggleDetail(
    { ...emptySelection(), mode: "all_matching", excludedIds: ["run"] },
    "run",
  );
  expect(selection.excludedIds).toEqual([]);
});
