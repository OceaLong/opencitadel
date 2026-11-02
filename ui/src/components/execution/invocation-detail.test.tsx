// @vitest-environment jsdom
import { expect, test, vi } from "vitest";

import type { StepView } from "@/lib/api/types/execution-view";

import { renderComponent } from "@/test-utils/render";

import { InvocationDetail } from "./invocation-detail";

vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));

test("no-send approval drift shows exact frozen subject with no approval reuse action", async () => {
  const step: StepView = {
    step_id: "step",
    relationship: "direct",
    schema_version: 1,
    run_id: "run",
    kind: "tool",
    status: "failed",
    projection_revision: 4,
    completeness: { state: "complete", missing_fields: [], missing_intervals: [] },
    tool_name: "scale_workload",
    end_reason: "TOOL_CONTRACT_CHANGED",
    contract_drift: {
      reason: "contract_changed",
      subject_digest: "a".repeat(64),
      tool_name: "scale_workload",
    },
  };
  const r = await renderComponent(<InvocationDetail detail={step} />);
  expect(r.container.querySelector('[role="alert"]')?.textContent).toContain("contractDrift");
  expect(r.container.textContent).toContain("a".repeat(64));
  expect(r.container.querySelector("button")).toBeNull();
  await r.unmount();
});
