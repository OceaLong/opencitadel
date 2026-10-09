import { expect, test } from "vitest";

import { baselineComparison } from "./baseline";
const row = {
  identity: [],
  left: "a",
  right: "b",
  mean_left: 4,
  mean_right: 2,
  delta: 2,
  relative_delta: 1,
  case_count: 20,
  confidence_interval: [1, 3] as [number, number],
  bootstrap_samples: 2000,
  seed: 0,
  interpretation: "paired_descriptive",
};
test("saved right baseline keeps direction and left baseline reorients exact interval", () => {
  expect(baselineComparison(row, "b")).toMatchObject({
    configuration: "a",
    delta: 2,
    relative: 1,
    interval: [1, 3],
  });
  expect(baselineComparison(row, "a")).toMatchObject({
    configuration: "b",
    delta: -2,
    relative: -0.5,
    interval: [-3, -1],
  });
});
test("no baseline substitute and zero denominator differs from no paired observations", () => {
  expect(baselineComparison(row, null)).toBeNull();
  expect(baselineComparison(row, "revoked")).toBeNull();
  expect(baselineComparison({ ...row, mean_left: 0 }, "a")?.relativeState).toBe("zeroBaseline");
  expect(
    baselineComparison({ ...row, case_count: 0, delta: null, mean_left: null }, "a"),
  ).toMatchObject({ delta: null, relative: null, interval: null, relativeState: "unavailable" });
});
