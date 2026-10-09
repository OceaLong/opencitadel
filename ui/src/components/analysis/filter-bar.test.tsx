// @vitest-environment jsdom
import { act, useState } from "react";
import { expect, test, vi } from "vitest";

import type { SummaryQuery } from "@/lib/api/types/execution-analysis";

import { renderComponent } from "@/test-utils/render";

import { FilterBar } from "./filter-bar";
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
test("configuration typing preserves trailing separators, accepts five and rejects six on form submission", async () => {
  const apply = vi.fn();
  function Form() {
    const [value, onChange] = useState<SummaryQuery>({
      filters: { start: "2026-09-01T00:00:00Z", end: "2026-09-09T00:00:00Z" },
      timezone: "UTC",
      grain: "day",
    });
    return (
      <FilterBar value={value} onChange={onChange} onApply={() => apply(value)} pending={false} />
    );
  }
  const view = await renderComponent(<Form />);
  const input = [...view.container.querySelectorAll("label")]
    .find((l) => l.textContent?.includes("comparisonConfigurations"))!
    .querySelector("input")!;
  const type = async (value: string) =>
    act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
  await type("one,");
  expect(input.value).toBe("one,");
  await type("one,two,three,four,five");
  await act(async () => view.container.querySelector("form")!.requestSubmit());
  expect(apply).toHaveBeenCalledTimes(1);
  expect(apply.mock.calls[0][0].filters.comparison_config_version_ids).toHaveLength(5);
  await type("one,two,three,four,five,six");
  await act(async () => view.container.querySelector("form")!.requestSubmit());
  expect(input.checkValidity()).toBe(false);
  expect(apply).toHaveBeenCalledTimes(1);
  await view.unmount();
});
