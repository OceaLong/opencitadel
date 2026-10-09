import { expect, test } from "vitest";

import { equalCaseValues, numericScore, summarizeDistribution } from "./matrix";
test("missing score is distinct from zero", () => {
  expect(numericScore(null)).toBeNull();
  expect(numericScore(0)).toBe("0");
});
test("valid repeats average within case before cases receive equal weight", () => {
  expect(
    equalCaseValues([
      { caseId: "a", value: 4 },
      { caseId: "a", value: 2 },
      { caseId: "b", value: 0 },
      { caseId: "c", value: null },
    ]),
  ).toEqual([3, 0]);
});
test("box summaries require five valid cases and preserve real zero", () => {
  expect(summarizeDistribution([0, 1, 2, 4])).toBeNull();
  expect(summarizeDistribution([4, 0, 2, 1, 3])).toEqual({
    min: 0,
    q1: 1,
    median: 2,
    q3: 3,
    max: 4,
    n: 5,
  });
});
