// @vitest-environment jsdom
import { expect, test, vi } from "vitest";

import type { AnalysisSummary } from "@/lib/api/types/execution-analysis";

import { renderComponent } from "@/test-utils/render";

import { RunCharts } from "./run-charts";
vi.mock("recharts", async () => {
  const recharts = await vi.importActual<typeof import("recharts")>("recharts");
  const { cloneElement } = await import("react");
  return {
    ...recharts,
    ResponsiveContainer: ({
      children,
    }: {
      children: import("react").ReactElement<{ width: number; height: number }>;
    }) => cloneElement(children, { width: 600, height: 208 }),
  };
});
vi.mock("next-intl", () => ({
  useLocale: () => "en",
  useTranslations: () => (key: string) => key,
}));
const m = {
  value: 0,
  unit: "ms",
  numerator: null,
  denominator: null,
  sample_count: 1,
  missing_count: 1,
  excluded_count: 1,
};
const latency = {
  scheme: "execution-latency-ms-v1" as const,
  edges_ms: [0, 100],
  edge_convention: "lower_inclusive_upper_exclusive" as const,
  bin_counts: [1],
  overflow: { lower_ms: 100, count: 0, maximum_ms: null },
  p50: m,
  p95: m,
  samples: [{ run_id: "run-zero", duration_ms: 0 }],
};
const summary: AnalysisSummary = {
  grain: "day",
  timezone: "UTC",
  watermark: "fixed",
  metric_version: "v1",
  metrics: {
    series: [],
    charts: {
      latency,
      latency_groups: [],
      tools: {
        availability: "available",
        items: [
          {
            tool_name: "long-tool",
            errors: 0,
            terminal: 0,
            execution_errors: 0,
            business_errors: 0,
            excluded: 1,
            unknown: 1,
            deferred: 0,
            cancelled: 0,
            error_rate: { ...m, value: null, unit: "ratio", numerator: 0, denominator: 0 },
          },
        ],
      },
    },
  },
};
test("sparse exact data preserves zero latency and unknown denominator", async () => {
  const view = await renderComponent(
    <RunCharts
      summary={summary}
      context={{
        start: "start",
        end: "end",
        watermark: "fixed",
        timezone: "UTC",
        metricVersion: "v1",
      }}
      onRun={() => {}}
      onTool={() => {}}
    />,
  );
  expect(view.container.textContent).toContain("sparseHistogram");
  expect(view.container.textContent).toContain("run-zero");
  expect(view.container.textContent).toContain("unknown (0/0)");
  expect(view.container.textContent).toContain("smallSample");
  expect(view.container.textContent).toContain("missing=1");
  await view.unmount();
});

for (const [count, missing, points, curves] of [
  [7, false, 21, 0],
  [7, true, 18, 0],
  [8, false, 0, 3],
] as const) {
  test(`${count} observed buckets render ${missing ? "only available discrete values" : count < 8 ? "discrete points" : "trend lines"}`, async () => {
    const buckets = Array.from({ length: count }, (_, index) =>
      new Date(Date.UTC(2026, 8, 1 + index * (count < 8 ? 2 : 1))).toISOString(),
    );
    const view = await renderComponent(
      <RunCharts
        summary={{
          ...summary,
          metrics: {
            ...summary.metrics,
            series: buckets.map((bucket, index) => ({
              group: {
                bucket,
                configuration_revision: "c",
                family: "agent",
                execution_mode: "production",
                purpose: "production",
              },
              metrics: Object.fromEntries(
                ["success_rate", "latency_p50", "latency_p95"].map((key) => [
                  key,
                  { ...m, value: missing && index === 3 ? null : index === 0 ? 0 : 1 },
                ]),
              ),
            })),
          },
        }}
        context={{
          start: buckets[0],
          end: buckets.at(-1)!,
          watermark: "fixed",
          timezone: "UTC",
          metricVersion: "v1",
        }}
        onRun={() => {}}
        onTool={() => {}}
      />,
    );
    expect(view.container.querySelectorAll(".recharts-line-curve")).toHaveLength(curves);
    expect(view.container.querySelectorAll(".recharts-scatter-symbol")).toHaveLength(points);
    // Intervening unobserved grid slots and null measures never become points.
    if (count < 8) {
      expect(view.container.querySelectorAll(".recharts-scatter-line")).toHaveLength(0);
      expect(view.container.textContent).toContain("sparseTrend");
    }
    await view.unmount();
  });
}

test("hourly data table distinguishes both instants in a repeated local DST hour", async () => {
  const buckets = ["2026-11-01T05:30:00Z", "2026-11-01T06:30:00Z"];
  const view = await renderComponent(
    <RunCharts
      summary={{
        ...summary,
        grain: "hour",
        timezone: "America/New_York",
        metrics: {
          ...summary.metrics,
          series: buckets.map((bucket) => ({
            group: {
              bucket,
              configuration_revision: "c",
              family: "agent",
              execution_mode: "production",
              purpose: "production",
            },
            metrics: { success_rate: { ...m, value: 1 } },
          })),
        },
      }}
      context={{
        start: buckets[0],
        end: buckets[1],
        watermark: "fixed",
        timezone: "America/New_York",
        metricVersion: "v1",
      }}
      onRun={() => {}}
      onTool={() => {}}
    />,
  );
  const tableText = [...view.container.querySelectorAll("table time")].map(
    (node) => node.textContent,
  );
  expect(tableText).toContain("2026-11-01T05:30:00.000Z · Nov 1, 2026, 01:30 AM GMT-4");
  expect(tableText).toContain("2026-11-01T06:30:00.000Z · Nov 1, 2026, 01:30 AM GMT-5");
  await view.unmount();
});

test("all-missing evaluation scores do not render an empty numeric distribution", async () => {
  const { EvaluationCharts } = await import("./analysis-charts");
  const view = await renderComponent(
    <EvaluationCharts
      points={[{ result_id: "r", case_id: "c", config_id: "v", value: null, cost_usd: null }]}
      labels={{}}
      source="rule"
      dimension="quality"
      revision={1}
      usageWatermark="fixed"
      reviewStatus="complete"
    />,
  );
  expect(view.container.querySelector('[role="img"][aria-label="scoreDistribution"]')).toBeNull();
  expect(view.container.textContent).toContain("missingScore");
  await view.unmount();
});
