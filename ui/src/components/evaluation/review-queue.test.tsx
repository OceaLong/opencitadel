// @vitest-environment jsdom
import { act } from "react";
import { expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";
const mocks = vi.hoisted(() => ({ reviews: vi.fn() }));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));
vi.mock("@/lib/api/evaluations", () => ({ evaluationApi: { reviews: mocks.reviews } }));
import { ReviewQueue } from "./review-queue";
test("an empty live scan still follows next_cursor and preserves selected result and score cut", async () => {
  mocks.reviews
    .mockResolvedValueOnce({ items: [], next_cursor: "opaque-next" })
    .mockResolvedValueOnce({
      items: [
        {
          batch_id: "batch",
          result_id: "result",
          run_id: "run",
          evaluation_revision: 9,
          result_revision: 4,
          rubric_version: "rubric",
          review_status: "pending",
          required_dimensions: ["quality"],
          received_dimensions: [],
        },
      ],
      next_cursor: null,
    });
  const view = await renderComponent(
    <ReviewQueue
      access={{
        workspaceId: "workspace",
        canManage: false,
        canRun: false,
        canRegister: false,
        canReview: true,
      }}
    />,
  );
  expect(view.container.querySelector("button")).not.toBeNull();
  await act(async () => {
    view.container.querySelector("button")!.click();
  });
  expect(mocks.reviews.mock.calls[1][1]).toBe("opaque-next");
  const href = view.container.querySelector("a")!.getAttribute("href")!;
  expect(href).toContain("result=result");
  expect(href).toContain("score_revision=9");
  expect(href).toContain("run=run");
  await view.unmount();
});
