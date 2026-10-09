// @vitest-environment jsdom
import { act } from "react";
import { expect, test, vi } from "vitest";

import { navigationOwner, readNavigationContext } from "@/lib/analysis-view/navigation-context";
import { analysisApi } from "@/lib/api/execution-analysis";
import type { ComparisonEnvelope } from "@/lib/api/types/execution-analysis";

import { renderComponent } from "@/test-utils/render";

import { ComparisonWorkspace } from "./comparison-workspace";
const nav = vi.hoisted(() => ({ search: new URLSearchParams(), push: vi.fn(), replace: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => nav, useSearchParams: () => nav.search }));
vi.mock("next-intl", () => ({
  useLocale: () => "en",
  useTranslations: () => (key: string) => key,
}));
vi.mock("@/lib/api/execution-analysis", () => ({
  analysisApi: { getComparison: vi.fn(), align: vi.fn() },
}));
vi.mock("./run-charts", () => ({
  RunCharts: ({ onRun }: { onRun: (id: string) => void }) => (
    <button onClick={() => onRun("r")}>open run</button>
  ),
}));
vi.mock("./metric-summary", () => ({ MetricSummary: () => null }));
vi.mock("./metric-facts", () => ({ MetricFacts: () => null }));
vi.mock("./evaluation-series", () => ({ RetainedEvaluationSeries: () => null }));
vi.mock("./export-panel", () => ({ ExportPanel: () => null }));
vi.mock("./artifact-diff", () => ({ ArtifactDiff: () => null }));
vi.mock("@/components/execution/trace-view", () => ({
  TraceView: ({
    selection,
    onSelectStep,
  }: {
    selection: string | null;
    onSelectStep: (s: string) => void;
  }) => <button onClick={() => onSelectStep("step")}>{selection ?? "pick step"}</button>,
}));
vi.mock("@/components/execution/detail-panel", () => ({ DetailPanel: () => null }));
test("comparison drilldown restores exact revision, member page, two slots, step selection and scroll", async () => {
  const value = {
    comparison_id: "c",
    revision: 2,
    alignment_revision: 1,
    captured_at: "2026-09-01",
    timezone: "UTC",
    metrics: {},
    member_count: 1,
    members: [],
    details: [
      {
        run_id: "r",
        availability: "available",
        body: { steps: [{ run_id: "r", step_id: "step", artifact_refs: [] }] },
      },
    ],
    alignments: [],
    suggestions: [],
    context: {
      start: "2026-09-01",
      end: "2026-09-02",
      grain: "day",
      filters: {},
      detail_run_ids: ["r"],
    },
    next_cursor: "next-page",
  } as unknown as ComparisonEnvelope;
  vi.mocked(analysisApi.getComparison).mockResolvedValue(value);
  const access = {
    ownerKey: "u",
    workspaceId: "",
    canManage: true,
    canRun: false,
    canRegister: false,
  };
  const view = await renderComponent(<ComparisonWorkspace access={access} id="c" revision={2} />);
  const left = [...view.container.querySelectorAll("label")]
    .find((l) => l.textContent?.includes("leftRun"))!
    .querySelector("select")!;
  await act(async () => {
    left.value = "r";
    left.dispatchEvent(new Event("change", { bubbles: true }));
  });
  const click = async (text: string) =>
    act(async () =>
      [...view.container.querySelectorAll("button")].find((b) => b.textContent === text)!.click(),
    );
  await click("next");
  await click("pick step");
  await click("open run");
  const target = new URL(nav.push.mock.calls[0][0], "http://localhost");
  const back = new URL(target.searchParams.get("analysis_return")!, "http://localhost");
  expect(target.toString().length).toBeLessThan(250);
  const context = readNavigationContext(
    "/analysis/comparisons/c",
    back.searchParams,
    navigationOwner(access),
  )!;
  expect(context.params.get("cursor")).toBe("next-page");
  expect(context.params.get("slots")).toBe('["r",null]');
  expect(context.params.get("steps")).toBe('["step",null]');
  expect(context.params.has("scroll")).toBe(true);
  await view.unmount();
  nav.search = back.searchParams;
  const restored = await renderComponent(
    <ComparisonWorkspace access={access} id="c" revision={2} />,
  );
  expect(analysisApi.getComparison).toHaveBeenLastCalledWith(
    "c",
    expect.objectContaining({ revision: 2, cursor: "next-page", detail_run_ids: ["r"] }),
    expect.anything(),
  );
  expect(restored.container.textContent).toContain("step");
  await restored.unmount();
});

test("successful alignment retains both selected steps so the pair can be unpaired", async () => {
  nav.search = new URLSearchParams();
  let alignmentRevision = 1;
  vi.mocked(analysisApi.getComparison).mockImplementation(
    async () =>
      ({
        comparison_id: "c",
        revision: 2,
        alignment_revision: alignmentRevision,
        captured_at: "2026-09-01",
        timezone: "UTC",
        metrics: {},
        member_count: 2,
        members: [],
        details: ["left", "right"].map((run_id) => ({
          run_id,
          availability: "available",
          body: { steps: [{ run_id, step_id: "step", attempt_id: "attempt", artifact_refs: [] }] },
        })),
        alignments: [],
        suggestions: [],
        context: {
          start: "2026-09-01",
          end: "2026-09-02",
          grain: "day",
          filters: {},
          detail_run_ids: ["left", "right"],
        },
      }) as unknown as ComparisonEnvelope,
  );
  vi.mocked(analysisApi.align).mockImplementation(
    async () =>
      ({ accepted_alignment_revision: ++alignmentRevision }) as unknown as ComparisonEnvelope,
  );
  const view = await renderComponent(
    <ComparisonWorkspace
      access={{
        ownerKey: "u",
        workspaceId: "",
        canManage: true,
        canRun: false,
        canRegister: false,
      }}
      id="c"
      revision={2}
    />,
  );
  for (const [index, runId] of ["left", "right"].entries()) {
    const select = [...view.container.querySelectorAll("label")]
      .find((label) => label.textContent?.includes(index === 0 ? "leftRun" : "rightRun"))!
      .querySelector("select")!;
    await act(async () => {
      select.value = runId;
      select.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }
  for (const runId of ["left", "right"]) {
    await act(async () => {
      view.container
        .querySelector(`[data-full-trace-owner="${runId}"] button`)!
        .dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
  }
  const button = (label: string) =>
    [...view.container.querySelectorAll("button")].find(
      (candidate) => candidate.textContent === label,
    )!;
  expect(button("unpair").disabled).toBe(false);
  await act(async () => button("confirmPair").click());
  expect(analysisApi.align).toHaveBeenCalledWith(
    "c",
    expect.objectContaining({
      expected_revision: 1,
      edits: [expect.objectContaining({ action: "confirm" })],
    }),
    expect.anything(),
  );
  expect(button("unpair").disabled).toBe(false);
  await act(async () => button("unpair").click());
  expect(analysisApi.align).toHaveBeenLastCalledWith(
    "c",
    expect.objectContaining({
      expected_revision: 2,
      edits: [expect.objectContaining({ action: "unpair" })],
    }),
    expect.anything(),
  );
  await view.unmount();
});
