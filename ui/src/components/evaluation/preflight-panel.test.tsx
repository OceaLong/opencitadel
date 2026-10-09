// @vitest-environment jsdom
import { act } from "react";
import { expect, test, vi } from "vitest";

import type { PreflightResult } from "@/lib/api/types/evaluations";

import { renderComponent } from "@/test-utils/render";
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
import { PreflightPanel } from "./preflight-panel";
const result = {
  allowed: true,
  quantity: 5000,
  physical_call_upper_bound: null,
  price_coverage: "unknown",
  token_budget: 500000,
  environment_ready: true,
  errors: [],
  warnings: [],
  revision: 1,
} as unknown as PreflightResult;
test("preflight is read-only until a permitted explicit confirmed start; new evidence needs new confirmation", async () => {
  const start = vi.fn(),
    check = vi.fn();
  const view = await renderComponent(
    <PreflightPanel result={result} pending={false} canRun onCheck={check} onStart={start} />,
  );
  const button = () =>
    Array.from(view.container.querySelectorAll("button")).find((b) => b.textContent === "start")!;
  expect(start).not.toHaveBeenCalled();
  expect(check).not.toHaveBeenCalled();
  expect(button().disabled).toBe(true);
  expect(view.container.textContent).toContain("unknown");
  await act(async () => {
    view.container.querySelector("input")!.click();
  });
  expect(button().disabled).toBe(false);
  await act(async () => {
    view.root.render(
      <PreflightPanel
        result={{ ...result, revision: 2 }}
        pending={false}
        canRun
        onCheck={check}
        onStart={start}
      />,
    );
  });
  expect(button().disabled).toBe(true);
  await act(async () => {
    view.root.render(
      <PreflightPanel
        result={result}
        pending={false}
        canRun={false}
        onCheck={check}
        onStart={start}
      />,
    );
  });
  expect(button().disabled).toBe(true);
  expect(start).not.toHaveBeenCalled();
  await view.unmount();
});
