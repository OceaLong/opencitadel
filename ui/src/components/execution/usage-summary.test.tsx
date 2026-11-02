// @vitest-environment jsdom
import { expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

import { ExecutionUsageSummary } from "./usage-summary";

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string, values?: { amount: string }) =>
    key === "amountUsd" ? `${values?.amount} USD` : key,
}));
test("main and summary costs retain separate counts and unknown calls", async () => {
  const counts = {
    calls: 1,
    known_input_count: 10,
    known_output_count: 2,
    unknown_usage_calls: 0,
    unknown_cost_calls: 0,
    known_cost_usd: "0.000000123",
  };
  const summary = { ...counts, calls: 3, known_cost_usd: "0.001", unknown_cost_calls: 2 };
  const view = await renderComponent(
    <ExecutionUsageSummary
      usage={{
        production: {
          ...counts,
          calls: 4,
          groups: { main_answer: counts, context_summary: summary },
        },
      }}
    />,
  );
  expect(view.container.querySelector('[data-usage-kind="main_answer"]')?.textContent).toContain(
    "0.000000123 USD",
  );
  const cells = view.container.querySelectorAll('[data-usage-kind="context_summary"] td');
  expect([...cells].map((cell) => cell.textContent)).toEqual(["3", "0.001 USD", "2"]);
  expect(view.container.textContent).toContain("knownOnly");
  await view.unmount();
});
test("older usage cuts do not invent summary attribution", async () => {
  const view = await renderComponent(
    <ExecutionUsageSummary
      usage={{
        production: {
          calls: 1,
          known_input_count: 0,
          known_output_count: 0,
          unknown_usage_calls: 1,
          unknown_cost_calls: 1,
          known_cost_usd: "0",
        },
      }}
    />,
  );
  expect(view.container.querySelector('[data-usage-kind="other"]')).not.toBeNull();
  expect(view.container.querySelector('[data-usage-kind="context_summary"]')).toBeNull();
  await view.unmount();
});
