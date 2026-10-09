// @vitest-environment jsdom
import { act } from "react";
import { expect, test, vi } from "vitest";

import type { RunView, StepView } from "@/lib/api/types/execution-view";

import { renderComponent } from "@/test-utils/render";
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
import { stageProgress, TaskView } from "./task-view";
export const run = {
  run_id: "r",
  status: "running",
  public_summary: "Goal",
  capabilities: [],
  completeness: { state: "complete", missing_fields: [], missing_intervals: [] },
} as unknown as RunView;
test("no fabricated percentage for an unplanned run", () => {
  expect(stageProgress(3, null)).toBeNull();
  expect(stageProgress(3, 4)).toBe(75);
});
test("observed activities, approval and clarification stay independent and capability gated", async () => {
  const steps = [
    { step_id: "ask", kind: "clarification", status: "waiting", public_summary: "Which region?" },
    { step_id: "tool", kind: "tool", status: "completed", business_outcome: "failure" },
  ] as StepView[];
  const { container, unmount } = await renderComponent(
    <TaskView
      run={run}
      steps={steps}
      approvals={[{ approval_id: "approval", status: "pending" }]}
      onSelectStep={() => {}}
      onSelectArtifact={() => {}}
      onOpenApproval={() => {}}
    />,
  );
  expect(container.querySelector('[role="progressbar"]')).toBeNull();
  expect(container.textContent).toContain("observedActivities");
  expect(container.textContent).toContain("pendingApprovals");
  expect(container.textContent).toContain("pendingClarifications");
  expect(container.textContent).toContain("businessFailure");
  expect(container.textContent).toContain("readOnlyReason");
  expect(container.querySelector<HTMLButtonElement>("[data-approval]")?.disabled).toBe(true);
  await unmount();
});

test("Task source reference carries canonical citation and its producing step", async () => {
  const select = vi.fn();
  const steps = [
    {
      step_id: "s1",
      run_id: "r",
      kind: "tool",
      status: "completed",
      citation_refs: [{ citation_id: "c1", availability: "available" }],
    },
  ] as StepView[];
  const { container, unmount } = await renderComponent(
    <TaskView
      run={run}
      steps={steps}
      onSelectStep={() => {}}
      onSelectArtifact={() => {}}
      onOpenApproval={() => {}}
      onSelectCitation={select}
    />,
  );
  const button = container.querySelector('[data-task-citation="c1"]') as HTMLButtonElement;
  expect(button).not.toBeNull();
  button.click();
  expect(select).toHaveBeenCalledWith("c1", "s1");
  await unmount();
});

test("visible task rows use roving focus without selecting until activated", async () => {
  const select = vi.fn();
  const result = await renderComponent(
    <TaskView
      run={run}
      steps={
        [
          { step_id: "a", status: "completed" },
          { step_id: "b", status: "completed" },
        ] as StepView[]
      }
      onSelectStep={select}
      onSelectArtifact={vi.fn()}
      onOpenApproval={vi.fn()}
    />,
  );
  const rows = result.container.querySelectorAll<HTMLButtonElement>("li button");
  expect(rows[0].tabIndex).toBe(0);
  expect(rows[1].tabIndex).toBe(-1);
  await act(async () => {
    rows[0].focus();
    rows[0].dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
  });
  expect(document.activeElement).toBe(rows[1]);
  expect(select).not.toHaveBeenCalled();
  await act(async () => rows[1].click());
  expect(select).toHaveBeenCalledWith("b");
  await result.unmount();
});

test("accepted public content has fresh text owners and retained refresh is non-ready", async () => {
  const base = {
    run: { ...run, projection_revision: 5 },
    steps: [
      {
        step_id: "s",
        run_id: "r",
        kind: "model",
        status: "running",
        projection_revision: 5,
        public_summary: "Received fragments: 2",
      },
    ] as StepView[],
    onSelectStep: vi.fn(),
    onSelectArtifact: vi.fn(),
    onOpenApproval: vi.fn(),
  };
  const view = await renderComponent(
    <TaskView {...base} renderIdentity={{ scopeId: "team", at: "cut-5", ready: true }} />,
  );
  const owner = view.container.querySelector('[data-native-content="progress"]');
  expect(owner?.textContent).toBe("Received fragments: 2");
  expect(owner?.getAttribute("elementtiming")).toBe("execution-progress");
  expect(
    view.container.querySelector('[data-native-ready="true"]')?.getAttribute("data-public-at"),
  ).toBe("cut-5");
  await act(async () =>
    view.root.render(
      <TaskView {...base} renderIdentity={{ scopeId: "team", at: "cut-5", ready: false }} />,
    ),
  );
  expect(view.container.querySelector('[data-native-ready="true"]')).toBeNull();
  await act(async () =>
    view.root.render(
      <TaskView
        {...base}
        steps={[
          { ...base.steps[0], projection_revision: 6, public_summary: "Received fragments: 3" },
        ]}
        renderIdentity={{ scopeId: "team", at: "cut-6", ready: true }}
      />,
    ),
  );
  expect(view.container.querySelector('[data-native-content="progress"]')).not.toBe(owner);
  expect(owner?.textContent).toBe("Received fragments: 2");
  await view.unmount();
});
