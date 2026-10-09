// @vitest-environment jsdom
import { expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

import { MetricFacts } from "./metric-facts";
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
test("accounting facts preserve precise cost strings and explicit missing coverage", async () => {
  const metric = {
    value: "0.000000000000000001",
    unit: "USD",
    numerator: null,
    denominator: null,
    sample_count: 1,
    missing_count: 2,
    excluded_count: 0,
  };
  const result = await renderComponent(
    <MetricFacts
      metrics={{
        usage: {
          grain: "run",
          accounting_run_count: 3,
          purposes: {
            evaluation_subject: { cost_usd: metric },
            evaluation_judge: { cost_usd: { ...metric, value: null } },
          },
        },
      }}
    />,
  );
  expect(result.container.textContent).toContain("0.000000000000000001");
  expect(result.container.textContent).toContain("evaluation_judge");
  expect(result.container.textContent).toContain("unknown");
  expect(result.container.textContent).toContain("missing=2");
  await result.unmount();
});
