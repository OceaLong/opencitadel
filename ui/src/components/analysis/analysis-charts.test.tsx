// @vitest-environment jsdom
import { act } from "react";
import { expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

import { EvaluationCharts } from "./analysis-charts";
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
test("exact table action selects an off-page result at the chart score cut", async () => {
  const pick = vi.fn();
  const view = await renderComponent(
    <EvaluationCharts
      points={[
        { result_id: "off-page", case_id: "case", config_id: "config", value: 3, cost_usd: "0.02" },
      ]}
      labels={{ config: "Config" }}
      source="model"
      dimension="quality"
      revision={7}
      usageWatermark="cut"
      reviewStatus="pending"
      onSelectResult={pick}
    />,
  );
  const button = Array.from(view.container.querySelectorAll("button")).find(
    (b) => b.textContent === "off-page",
  );
  expect(button).toBeDefined();
  await act(async () => button!.click());
  expect(pick).toHaveBeenCalledWith("off-page", 7);
  await view.unmount();
});

test("each chart labels sample coverage at its own grain", async () => {
  const metadata = {
    sample_count: 3,
    missing_count: 2,
    excluded_count: 1,
    grain: "case_config",
    timezone: "UTC",
    metric_version: "distribution-v1",
  };
  const view = await renderComponent(
    <EvaluationCharts
      points={[]}
      labels={{}}
      source="model"
      dimension="quality"
      revision={7}
      usageWatermark="cut"
      reviewStatus="pending"
      distributionMetadata={metadata}
      qualityCostMetadata={{
        ...metadata,
        sample_count: 9,
        missing_count: 4,
        grain: "case_result",
        metric_version: "cost-v1",
      }}
    />,
  );
  const paragraphs = Array.from(view.container.querySelectorAll("p"));
  expect(
    paragraphs.some(
      (p) =>
        p.textContent?.includes("sampleCoverage: 3") && p.textContent?.includes("distribution-v1"),
    ),
  ).toBe(true);
  expect(
    paragraphs.some(
      (p) => p.textContent?.includes("sampleCoverage: 9") && p.textContent?.includes("cost-v1"),
    ),
  ).toBe(true);
  await view.unmount();
});
