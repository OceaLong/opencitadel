// @vitest-environment jsdom
import { act, StrictMode } from "react";
import { beforeEach, expect, test, vi } from "vitest";

import { ApiError } from "@/lib/api/fetch";

import { renderComponent } from "@/test-utils/render";
const mocks = vi.hoisted(() => ({
  batch: vi.fn(),
  batchEnvironments: vi.fn(),
  summary: vi.fn(),
  scores: vi.fn(),
  version: vi.fn(),
  review: vi.fn(),
  reviewContext: vi.fn(),
  cancelJudge: vi.fn(),
  reviewCommand: vi.fn(),
  batchEvents: vi.fn(),
  search: new URLSearchParams("result=r"),
}));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("next/navigation", () => ({ useSearchParams: () => mocks.search }));
vi.mock("@/lib/api/evaluations", () => ({ evaluationApi: mocks }));
vi.mock("@/components/analysis/analysis-charts", () => ({ EvaluationCharts: () => null }));
import { BatchOverview } from "./batch-overview";
const access = {
  workspaceId: "w",
  canManage: false,
  canRun: false,
  canRegister: false,
  canReview: true,
};
beforeEach(() => {
  vi.clearAllMocks();
  mocks.batchEnvironments.mockResolvedValue({ items: [], next_cursor: null });
  mocks.search = new URLSearchParams("result=r");
  mocks.batch.mockResolvedValue({
    id: "b",
    revision: 4,
    status: "completed",
    counts: { succeeded: 1 },
    review_status: "pending",
    cleanup_status: "clean",
  });
  mocks.summary.mockResolvedValue({
    snapshot_id: "snap",
    evaluation_revision: 7,
    source: "human",
    dimension: "correctness",
    rubric_id: "rubric",
    usage_watermark: "2026-01-01",
    items: [
      {
        id: "r",
        case_id: "case",
        case_label: "Case",
        config_id: "config",
        config_label: "Config",
        result_revision: 4,
        repetition: 0,
        attempt: 0,
        run_id: "run",
        score_run_id: "run",
        execution_status: "succeeded",
        scoring_status: "complete",
        value: 3,
      },
    ],
    points: [],
    allocations: [],
    next_cursor: null,
  });
  mocks.scores.mockResolvedValue({
    evaluation_revision: 7,
    next_cursor: null,
    items: [
      {
        id: "s",
        result_id: "r",
        result_revision: 3,
        run_id: "run",
        run_revision: 9,
        evaluation_revision: 7,
        author: "reviewer",
        timestamp: "2026-01-01",
        score: {
          source: "human",
          dimension: "correctness",
          rubric_revision: "rubric",
          value: 3,
          status: "valid",
          reason: "Original accepted reason",
          evidence: [],
        },
      },
    ],
  });
  mocks.version.mockResolvedValue({
    id: "rubric",
    name: "Rubric",
    dimensions: [{ id: "correctness", name: "Correctness", anchors: ["a", "b", "c", "d", "e"] }],
  });
  mocks.reviewContext.mockImplementation(async () => ({
    result_id: "r",
    batch_id: "b",
    result_revision: 4,
    evaluation_revision: 7,
    rubric: await mocks.version(),
    applicable_dimensions: ["correctness"],
    human_heads: [
      {
        id: "s",
        dimension: "correctness",
        value: 3,
        status: "valid",
        reason: "Original accepted reason",
        evidence: [],
        evaluation_revision: 7,
      },
    ],
  }));
  mocks.batchEvents.mockImplementation(() => new Promise(() => {}));
});
test("review 409 keeps accepted history and offers refresh with exact independent CAS values", async () => {
  mocks.review.mockRejectedValue(new ApiError(409, "conflict"));
  const view = await renderComponent(<BatchOverview id="b" access={access} />);
  expect(view.container.querySelector("form")).not.toBeNull();
  await act(async () => {
    view.container
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  expect(mocks.review.mock.calls[0][1]).toMatchObject({
    expected_revision: 7,
    expected_result_revision: 4,
    rubric_version: "rubric",
  });
  expect(view.container.querySelector('[role="alert"]')?.textContent).toContain("conflict");
  expect(view.container.textContent).toContain("Original accepted reason");
  await view.unmount();
});
test("disconnected stream refresh retains explicit score revision", async () => {
  mocks.search = new URLSearchParams("result=r&score_revision=7");
  mocks.batchEvents
    .mockResolvedValueOnce(undefined)
    .mockImplementation(() => new Promise(() => {}));
  const view = await renderComponent(<BatchOverview id="b" access={access} />);
  expect(mocks.summary.mock.calls.length).toBeGreaterThanOrEqual(1);
  expect(mocks.summary.mock.calls.every((call) => call[2].evaluation_revision === "7")).toBe(true);
  expect(view.container.textContent).toContain("pinnedScore");
  expect(view.container.querySelector("form")).toBeNull();
  await view.unmount();
});

test("StrictMode replay still initializes authoritative summary", async () => {
  const view = await renderComponent(
    <StrictMode>
      <BatchOverview id="b" access={access} />
    </StrictMode>,
  );
  expect(view.container.querySelector("tbody td button")).not.toBeNull();
  await view.unmount();
});

test("cancel judge binds the durable receipt result, Run and independent revisions", async () => {
  mocks.review.mockResolvedValue({
    id: "intent",
    result_id: "r",
    result_revision: 4,
    evaluation_revision: 8,
    kind: "rescore",
    status: "submitted",
    judge_run_id: "judge",
  });
  mocks.cancelJudge.mockResolvedValue({
    id: "cancel",
    result_id: "r",
    result_revision: 4,
    evaluation_revision: 9,
    kind: "cancel",
    status: "accepted",
  });
  const view = await renderComponent(<BatchOverview id="b" access={access} />);
  await act(async () => {
    view.container
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  const cancel = Array.from(view.container.querySelectorAll("button")).find(
    (b) => b.textContent === "cancelJudge",
  )!;
  expect(cancel).toBeDefined();
  await act(async () => cancel.click());
  expect(mocks.cancelJudge.mock.calls[0].slice(0, 2)).toEqual([
    "r",
    expect.objectContaining({
      expected_result_revision: 4,
      judge_run_id: "judge",
      expected_revision: 7,
    }),
  ]);
  await view.unmount();
});

test("refresh preserves the loaded extent and one new snapshot", async () => {
  const base = await mocks.summary();
  let capture = 0;
  mocks.summary.mockImplementation(async (_id, _o, q) => {
    if (!q.cursor) capture++;
    const page = q.cursor ? 1 : 0;
    return {
      ...base,
      snapshot_id: `snap${capture}`,
      items: Array.from({ length: 100 }, (_, i) => ({
        ...base.items[0],
        id: `r${page * 100 + i}`,
        case_id: `case${page * 100 + i}`,
        case_label: `Capture ${capture} Case ${page * 100 + i}`,
      })),
      evaluation_revision: capture >= 3 ? 8 : 7,
      next_cursor: page ? null : `cursor${capture}`,
      points: [],
    };
  });
  const view = await renderComponent(<BatchOverview id="b" access={access} />);
  await act(async () =>
    Array.from(view.container.querySelectorAll("button"))
      .find((b) => b.textContent === "loadMore")!
      .click(),
  );
  expect(view.container.querySelector("[aria-rowcount]")?.getAttribute("aria-rowcount")).toBe(
    "201",
  );
  const matrix = view.container.querySelector("[aria-rowcount]")!.parentElement!;
  await act(async () => {
    matrix.scrollTop = 8800;
    matrix.dispatchEvent(new Event("scroll", { bubbles: true }));
  });
  await act(async () =>
    Array.from(view.container.querySelectorAll("button"))
      .find((b) => b.textContent === "refresh")!
      .click(),
  );
  expect(matrix.scrollTop).toBe(8800);
  expect(view.container.textContent).toContain("Capture 2 Case 100");
  expect(view.container.querySelector("[aria-rowcount]")?.getAttribute("aria-rowcount")).toBe(
    "201",
  );
  expect(mocks.summary.mock.calls.at(-1)?.[2].cursor).toBe("cursor2");
  await act(async () =>
    Array.from(view.container.querySelectorAll("button"))
      .find((b) => b.textContent === "refresh")!
      .click(),
  );
  expect(matrix.scrollTop).toBe(8800);
  expect(view.container.textContent).toContain("Capture 3 Case 100");
  expect(view.container.textContent).not.toContain("Capture 2 Case");
  await view.unmount();
});

test("review draft survives unrelated revision and conflict refresh requires reconciliation", async () => {
  const view = await renderComponent(<BatchOverview id="b" access={access} />);
  const input = view.container.querySelector<HTMLInputElement>(
    `[id="${Array.from(view.container.querySelectorAll("label")).find((e) => e.textContent === "reason")!.htmlFor}"]`,
  )!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(
      input,
      "Draft survives",
    );
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  mocks.review.mockRejectedValueOnce(new ApiError(409, "conflict"));
  await act(async () =>
    view.container
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })),
  );
  expect(mocks.review.mock.calls[0][1].scores[0].reason).toBe("Draft survives");
  const base = await mocks.summary();
  mocks.summary.mockResolvedValue({ ...base, evaluation_revision: 8 });
  mocks.scores.mockResolvedValue({ ...(await mocks.scores()), evaluation_revision: 8 });
  mocks.reviewContext.mockResolvedValue({
    ...(await mocks.reviewContext()),
    evaluation_revision: 8,
  });
  await act(async () =>
    Array.from(view.container.querySelectorAll("button"))
      .find((b) => b.textContent === "refresh")!
      .click(),
  );
  expect(
    Array.from(view.container.querySelectorAll("input")).some((e) => e.value === "Draft survives"),
  ).toBe(true);
  expect(view.container.textContent).toContain("reconcileReview");
  await act(async () =>
    view.container
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })),
  );
  expect(mocks.review).toHaveBeenCalledTimes(1);
  await act(async () =>
    Array.from(view.container.querySelectorAll("button"))
      .find((b) => b.textContent === "reconcileReview")!
      .click(),
  );
  mocks.review.mockResolvedValueOnce({
    id: "next",
    result_id: "r",
    result_revision: 5,
    evaluation_revision: 9,
    status: "accepted",
    kind: "human",
  });
  await act(async () =>
    view.container
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })),
  );
  expect(mocks.review.mock.calls[1][1]).toMatchObject({
    expected_revision: 8,
    scores: [expect.objectContaining({ reason: "Draft survives" })],
  });
  await view.unmount();
});

test("same-cut result-only conflict refresh retains the draft and requires new result CAS reconciliation", async () => {
  const view = await renderComponent(<BatchOverview id="b" access={access} />);
  const input = view.container.querySelector<HTMLInputElement>(
    `[id="${Array.from(view.container.querySelectorAll("label")).find((e) => e.textContent === "reason")!.htmlFor}"]`,
  )!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(
      input,
      "Draft survives",
    );
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  const contextCalls = mocks.reviewContext.mock.calls.length;
  await act(async () =>
    Array.from(view.container.querySelectorAll("button"))
      .find((b) => b.textContent === "refresh")!
      .click(),
  );
  expect(mocks.reviewContext.mock.calls.length).toBeGreaterThan(contextCalls);
  expect(view.container.textContent).not.toContain("reconcileReview");
  expect(input.value).toBe("Draft survives");
  mocks.review.mockRejectedValueOnce(new ApiError(409, "conflict"));
  await act(async () =>
    view.container
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })),
  );
  expect(mocks.review.mock.calls[0][1].scores[0].reason).toBe("Draft survives");
  const base = await mocks.summary();
  mocks.summary.mockResolvedValue({
    ...base,
    items: base.items.map((row: { result_revision: number }) => ({ ...row, result_revision: 5 })),
  });

  mocks.reviewContext.mockResolvedValue({
    ...(await mocks.reviewContext()),
    result_revision: 5,
  });
  await act(async () =>
    Array.from(view.container.querySelectorAll("button"))
      .find((b) => b.textContent === "refresh")!
      .click(),
  );
  expect(
    Array.from(view.container.querySelectorAll("input")).some((e) => e.value === "Draft survives"),
  ).toBe(true);
  expect(view.container.textContent).toContain("reconcileReview");
  await act(async () =>
    view.container
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })),
  );
  expect(mocks.review).toHaveBeenCalledTimes(1);
  await act(async () =>
    Array.from(view.container.querySelectorAll("button"))
      .find((b) => b.textContent === "reconcileReview")!
      .click(),
  );
  mocks.review.mockResolvedValueOnce({
    id: "next",
    result_id: "r",
    result_revision: 5,
    evaluation_revision: 9,
    status: "accepted",
    kind: "human",
  });
  await act(async () =>
    view.container
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })),
  );
  expect(mocks.review.mock.calls[1][1]).toMatchObject({
    expected_revision: 7,
    expected_result_revision: 5,
    scores: [expect.objectContaining({ reason: "Draft survives" })],
  });
  await view.unmount();
});

test("pinned historical rubric shows every immutable custom anchor without current write context", async () => {
  mocks.search = new URLSearchParams("result=r&score_revision=7");
  mocks.version.mockResolvedValue({
    id: "rubric",
    name: "Historical custom rubric",
    dimensions: [
      {
        id: "quality",
        name: "Historical quality",
        anchors: ["Anchor zero", "Anchor one", "Anchor two", "Anchor three", "Anchor four"],
      },
      {
        id: "coverage",
        name: "Historical coverage",
        anchors: ["None", "Limited", "Partial", "Broad", "Complete"],
      },
    ],
  });
  const view = await renderComponent(<BatchOverview id="b" access={access} />);
  expect(mocks.reviewContext).not.toHaveBeenCalled();
  expect(view.container.querySelector("form")).toBeNull();
  for (const label of [
    "Historical quality",
    "Anchor zero",
    "Anchor four",
    "Historical coverage",
    "Complete",
  ])
    expect(view.container.textContent).toContain(label);
  expect(mocks.version).toHaveBeenCalledWith("rubrics", "rubric", expect.anything());
  await view.unmount();
});

test("environment current reuse and retained quarantine stay distinct after repair", async () => {
  const lease = {
    id: "lease",
    environment_version: "env",
    case_id: "case",
    config_version: "config",
    repeat: 1,
    generation: 1,
    revision: 5,
    state: "quarantine",
    reusable: false,
    prior_failed_operations: { reset: 1 },
  };
  mocks.batchEnvironments.mockResolvedValue({ items: [lease], next_cursor: null });
  const view = await renderComponent(<BatchOverview id="b" access={access} />);
  expect(view.container.querySelector('[data-lease-id="lease"]')?.textContent).toContain(
    "environmentNotReusable",
  );
  expect(view.container.querySelector('[data-lease-id="lease"]')?.textContent).toContain(
    "environmentPriorQuarantine",
  );
  await view.unmount();
  mocks.batchEnvironments.mockResolvedValue({
    items: [{ ...lease, revision: 8, state: "verified_clean", reusable: true }],
    next_cursor: null,
  });
  const repaired = await renderComponent(<BatchOverview id="b" access={access} />);
  const text = repaired.container.querySelector('[data-lease-id="lease"]')?.textContent;
  expect(text).toContain("environmentReusable");
  expect(text).toContain("environmentPriorQuarantine");
  expect(text).not.toContain("environmentNotReusable");
  await repaired.unmount();
});
