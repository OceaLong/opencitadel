// @vitest-environment jsdom
import { expect, test, vi } from "vitest";

import type { StepView } from "@/lib/api/types/execution-view";

import { renderComponent } from "@/test-utils/render";

import { ContextSummary } from "./context-summary";

vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));

const base: StepView = {
  step_id: "context-step",
  run_id: "run",
  kind: "activity",
  relationship: "direct",
  schema_version: 1,
  status: "completed",
  projection_revision: 5,
  completeness: { state: "complete", missing_fields: [], missing_intervals: [] },
};

test("context diagnostics show observed zero and keep unavailable counts absent", async () => {
  const r = await renderComponent(
    <ContextSummary
      step={{
        ...base,
        context: {
          stage: "prepare",
          state: "ready",
          summary_chunks: 0,
          original_sources: 150,
          definition_version: 2,
          definition_digest: "a".repeat(64),
        },
      }}
    />,
  );
  expect(r.container.querySelector('[role="status"]')?.textContent).toBe("state.ready");
  expect(r.container.textContent).toContain("summaryChunks0");
  expect(r.container.textContent).toContain("originalSources150");
  expect(r.container.textContent).not.toContain("inputEstimate");
  expect(r.container.querySelector("details")?.open).toBe(false);
  expect(r.container.querySelector("details")?.textContent).toContain("a".repeat(64));
  await r.unmount();
});

test("failed context retains the stable reason and legacy steps have no context card", async () => {
  const r = await renderComponent(
    <ContextSummary
      step={{
        ...base,
        status: "failed",
        end_reason: "CONTEXT_SOURCE_AUTHORIZATION_REVOKED",
        context: { stage: "compact" },
      }}
    />,
  );
  expect(r.container.querySelector('[role="status"]')?.textContent).toBe("state.unavailable");
  expect(r.container.textContent).toContain("CONTEXT_SOURCE_AUTHORIZATION_REVOKED");
  expect(r.container.querySelector("button")).toBeNull();
  await r.unmount();
  const legacy = await renderComponent(<ContextSummary step={base} />);
  expect(legacy.container.textContent).toBe("");
  await legacy.unmount();
});
