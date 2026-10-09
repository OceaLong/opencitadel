import { expect, test } from "vitest";

import { buildTrendGroups } from "./trend-dataset";
const metric = (value: number | null) => ({
  value,
  unit: "ratio",
  numerator: value,
  denominator: 1,
  sample_count: 1,
  missing_count: value === null ? 1 : 0,
  excluded_count: 0,
});
test("observed null rates count as observations and an absent internal bucket breaks lines", () => {
  const rows = Array.from({ length: 8 }, (_, i) => ({
    group: {
      family: "agent",
      purpose: "production",
      execution_mode: "production",
      configuration_revision: "c",
      bucket: new Date(Date.UTC(2026, 0, i < 4 ? i + 1 : i + 2)).toISOString(),
    },
    metrics: {
      success_rate: metric(i === 1 ? null : 0),
      latency_p50: metric(0),
      latency_p95: metric(null),
    },
  }));
  const [group] = buildTrendGroups(rows);
  expect(group.presentation).toBe("line");
  expect(group.rows[1].success).toBeNull();
  expect(group.rows[0].success).toBe(0);
  expect(group.rows[4].observed).toBe(false);
  expect(group.rows[4].success).toBeNull();
  expect(group.observed).toBe(8);
});
test("distinct semantic groups cannot be merged through a null configuration identity", () => {
  const row = {
    group: {
      family: "agent",
      purpose: "production",
      execution_mode: "production",
      configuration_revision: null,
      bucket: "2026-01-01T00:00:00Z",
    },
    metrics: { success_rate: metric(1) },
  };
  expect(
    buildTrendGroups([row, { ...row, group: { ...row.group, family: "patrol" } }]),
  ).toHaveLength(2);
});
