// @vitest-environment jsdom
import { act } from "react";
import { expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
import { ScorePanel } from "./score-panel";
test("separates three sources and displays old human value, missing state, exact score and Run revision", async () => {
  const base = {
    id: "s",
    result_id: "r",
    result_revision: 2,
    run_id: "run",
    run_revision: 8,
    evaluation_revision: 4,
    author: "reviewer",
    timestamp: "2026-01-01T00:00:00Z",
    supersedes_id: null,
    score: {
      source: "human" as const,
      dimension: "correctness",
      rubric_revision: "rubric",
      value: 0,
      status: "valid" as const,
      reason: "Original human rationale",
      evidence: [],
      recording: null,
    },
  };
  const view = await renderComponent(
    <ScorePanel
      result={{ id: "r", result_revision: 3 }}
      scoreRevision={{
        invalidations: [],
        items: [
          base,
          {
            ...base,
            id: "s2",
            evaluation_revision: 5,
            supersedes_id: "s",
            score: {
              ...base.score,
              value: null,
              status: "not_evaluable",
              reason: "Evidence unavailable",
            },
          },
        ],
        evaluation_revision: 5,
        next_cursor: null,
      }}
      onReview={vi.fn()}
      onRescore={vi.fn()}
      canReview={false}
    />,
  );
  expect(view.container.textContent).toContain("Original human rationale");
  expect(view.container.textContent).toContain("Evidence unavailable");
  expect(view.container.textContent).toContain("missingScore");
  expect(view.container.querySelectorAll("section[data-source]").length).toBe(3);
  expect(view.container.querySelector("form")).toBeNull();
  expect(view.container.querySelector("a")!.getAttribute("href")).toContain("score_revision=4");
  await view.unmount();
});

test("a sole custom dimension uses the visible applicable review target", async () => {
  const review = vi.fn();
  const rubric = {
    id: "custom",
    dimensions: [{ id: "quality", name: "Quality", anchors: ["0", "1", "2", "3", "4"] }],
  };
  const context = {
    result_id: "r",
    batch_id: "b",
    result_revision: 4,
    evaluation_revision: 8,
    rubric,
    applicable_dimensions: ["quality"],
    human_heads: [],
  };
  const view = await renderComponent(
    <ScorePanel
      result={{ id: "r", result_revision: 4 }}
      scoreRevision={{ items: [], evaluation_revision: 8, invalidations: [] }}
      rubric={rubric as never}
      reviewContext={context as never}
      onReview={review}
      onRescore={vi.fn()}
      canReview
    />,
  );
  await act(async () =>
    view.container
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })),
  );
  expect(review).toHaveBeenCalledWith(
    expect.objectContaining({ scores: [expect.objectContaining({ dimension: "quality" })] }),
  );
  await view.unmount();
});

test("chosen evidence remains in the draft when refreshed history omits its page", async () => {
  const evidence = { resource_kind: "file", resource_id: "evidence", revision: "1" };
  const rubric = {
    id: "rubric",
    dimensions: [{ id: "quality", name: "Quality", anchors: ["0", "1", "2", "3", "4"] }],
  };
  const context = {
    result_id: "r",
    batch_id: "b",
    result_revision: 4,
    evaluation_revision: 8,
    rubric,
    applicable_dimensions: ["quality"],
    human_heads: [],
  };
  const submit = vi.fn();
  const render = (items: unknown[]) => (
    <ScorePanel
      result={{ id: "r", result_revision: 4 }}
      scoreRevision={{ items: items as never, evaluation_revision: 8, invalidations: [] }}
      rubric={rubric as never}
      reviewContext={context as never}
      onReview={submit}
      onRescore={vi.fn()}
      canReview
    />
  );
  const view = await renderComponent(
    render([
      {
        id: "s",
        result_id: "r",
        score: { source: "human", dimension: "quality", value: 3, evidence: [evidence] },
      },
    ]),
  );
  await act(async () =>
    view.container.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click(),
  );
  await act(async () => view.root.render(render([])));
  await act(async () =>
    view.container
      .querySelector("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })),
  );
  expect(submit.mock.calls[0][0].scores[0].evidence).toEqual([evidence]);
  await view.unmount();
});
