import { expect, test } from "vitest";

import { evaluationSeries } from "./chart-contracts";
test("distribution averages valid repeats within case, keeps missing and requires five cases for a box", () => {
  const data = evaluationSeries([
    { result_id: "r1", case_id: "a", config_id: "c", value: 4, cost_usd: "1" },
    { result_id: "r2", case_id: "a", config_id: "c", value: 0, cost_usd: null },
    { result_id: "r3", case_id: "b", config_id: "c", value: null, cost_usd: null },
  ]);
  expect(data.groups[0].values).toEqual([2]);
  expect(data.groups[0].missing).toBe(1);
  expect(data.groups[0].box).toBeNull();
  expect(data.scatter).toBe(false);
});
test("scatter requires twelve distinct complete cases, not twelve repeats", () => {
  const rows = Array.from({ length: 12 }, (_, i) => ({
    result_id: String(i),
    case_id: "one",
    config_id: "c",
    value: 0,
    cost_usd: "0",
  }));
  expect(evaluationSeries(rows).scatter).toBe(false);
  expect(evaluationSeries(rows.map((r, i) => ({ ...r, case_id: String(i) }))).scatter).toBe(true);
});

test("exclusion and missing-price remain distinct at their own grains", () => {
  const series = evaluationSeries([
    {
      result_id: "invalid",
      case_id: "a",
      config_id: "c",
      value: null,
      cost_usd: "1",
      excluded: true,
    },
    {
      result_id: "missing",
      case_id: "b",
      config_id: "c",
      value: null,
      cost_usd: "1",
      excluded: false,
    },
    {
      result_id: "unpriced",
      case_id: "c",
      config_id: "c",
      value: 3,
      cost_usd: null,
      excluded: false,
    },
  ]);
  expect(series.groups[0]).toMatchObject({ missing: 1, excluded: 1 });
  expect(series.complete).toHaveLength(0);
});

test("analysis chart presentations preserve sparse observations at exact thresholds", async () => {
  const { trendPresentation, histogramPresentation, boxPresentation, scatterPresentation } =
    await import("./chart-contracts");
  expect(trendPresentation(3)).toBe("discrete");
  expect(trendPresentation(7)).toBe("discrete");
  expect(trendPresentation(8)).toBe("line");
  expect(histogramPresentation(19)).toBe("exact");
  expect(histogramPresentation(20)).toBe("histogram");
  expect(boxPresentation(4)).toBe("points");
  expect(boxPresentation(5)).toBe("box");
  expect(scatterPresentation(11)).toBe("table");
  expect(scatterPresentation(12)).toBe("scatter");
});
