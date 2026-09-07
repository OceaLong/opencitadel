// @vitest-environment jsdom
import { NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";

import { executionRecoveryApi } from "@/lib/api/execution-recovery";

import { renderComponent } from "@/test-utils/render";

import en from "../../../messages/en.json";
import { ExecutionRecoveryCard } from "./execution-recovery-card";

afterEach(() => vi.restoreAllMocks());
describe("execution recovery", () => {
  it("shows quarantined runs, scope lag, and durable recovery outcomes", async () => {
    vi.spyOn(executionRecoveryApi, "status").mockResolvedValue({
      scope_lags: [{ owner_scope_key: "user:u", lag: 4 }],
      poisoned_scopes: [],
      poisoned_runs: [
        {
          run_id: "run-one",
          owner_scope_key: "user:u",
          reason: "planner_error",
          last_error: "ValueError",
          failure_count: 3,
          next_attempt_at: null,
        },
      ],
      recovery_requests: [
        {
          id: "r",
          owner_scope_key: "user:u",
          status: "partial",
          reason: "operator",
          result: { remaining_run_ids: ["run-one"] },
        },
      ],
    });
    const view = await renderComponent(
      <NextIntlClientProvider locale="en" messages={en}>
        <ExecutionRecoveryCard />
      </NextIntlClientProvider>,
    );
    expect(view.container.textContent).toContain("run-one");
    expect(view.container.textContent).toContain("ValueError");
    expect(view.container.textContent).toContain("partial");
    expect(view.container.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(
      true,
    );
    await view.unmount();
  });
});
