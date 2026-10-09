// @vitest-environment jsdom
import { act } from "react";
import { expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";
const mocks = vi.hoisted(() => ({
  search: "run=r&at=history",
  push: vi.fn(),
  getView: vi.fn(),
  getStep: vi.fn(),
  readContent: vi.fn(),
  getTimeline: vi.fn(),
  getEvents: vi.fn(),
  streamEvents: vi.fn(),
  scope: { userId: "u", workspaceId: "team" },
}));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push, replace: vi.fn() }),
  usePathname: () => "/runs/r",
  useSearchParams: () => new URLSearchParams(mocks.search),
}));
vi.mock("@/providers/auth-provider", () => ({
  useAuth: () => ({ loading: false, user: { id: "u" } }),
}));
vi.mock("@/providers/client-data-provider", () => ({
  useClientDataScope: () => ({ scope: mocks.scope, scopeRevision: 1 }),
}));
vi.mock("@/lib/api/execution-view", () => ({ executionViewApi: mocks }));
vi.mock("@/components/markdown-content", () => ({
  MarkdownContent: ({ content }: { content: string }) => <p>{content}</p>,
}));
import { RunPageClient } from "./run-page-client";

test("read-only Run entry mounts real playback and replaces exact cut through shared coordinator", async () => {
  const completeness = { state: "complete", missing_fields: [], missing_intervals: [] };
  const page = {
    at: "history",
    revision: 1,
    steps: [],
    hidden_count: 0,
    next_cursor: null,
    run: {
      run_id: "r",
      projection_revision: 1,
      status: "running",
      public_summary: "Historical patrol",
      source: { entity_type: "patrol_run", entity_id: "p" },
      admitted_at: "2026-01-01T00:00:00Z",
      as_of: "2026-01-01T00:00:10Z",
      latest_available: "2026-01-01T00:01:00Z",
      completeness,
    },
    approvals: [],
    artifacts: [],
  };
  mocks.getView.mockResolvedValue(page);
  mocks.getEvents.mockResolvedValue({ events: [], next_cursor: null });
  mocks.streamEvents.mockImplementation(() => new Promise(() => {}));
  mocks.getTimeline.mockResolvedValue({ run_id: "r", at: "adjacent-tied-cut" });
  const { container, root, unmount } = await renderComponent(<RunPageClient runId="r" />);
  expect(container.textContent).toContain("Historical patrol");
  await act(async () =>
    Array.from(container.querySelectorAll("button"))
      .find((b) => b.textContent === "nextEvent")!
      .click(),
  );
  expect(container.textContent).not.toContain("Historical patrol");
  expect(mocks.getTimeline).toHaveBeenLastCalledWith(
    "r",
    expect.objectContaining({ anchor_at: "history", direction: "after" }),
    expect.objectContaining({ workspaceId: "team" }),
  );
  expect(mocks.push).toHaveBeenLastCalledWith(expect.stringContaining("at=adjacent-tied-cut"), {
    scroll: false,
  });
  mocks.search = "run=r&at=adjacent-tied-cut";
  mocks.getView.mockResolvedValue({
    ...page,
    at: "adjacent-tied-cut",
    revision: 2,
    run: { ...page.run, projection_revision: 2, public_summary: "Adjacent patrol" },
  });
  await act(async () => root.render(<RunPageClient runId="r" />));
  expect(container.textContent).toContain("Adjacent patrol");
  expect(container.querySelector("textarea")).toBeNull();
  await unmount();
});

test("actual non-session entry shares exact-cut body and drops a late response on seek", async () => {
  mocks.search = "run=r&at=history&step=step&panel=input-output";
  const completeness = { state: "complete", missing_fields: [], missing_intervals: [] };
  const step = {
    step_id: "step",
    run_id: "r",
    at: "history",
    projection_revision: 1,
    kind: "model",
    status: "completed",
    attempt_id: "attempt",
    public_summary: "Public model result",
    output_ref: { content_id: "o", availability: "available" },
    completeness,
  };
  mocks.getView.mockResolvedValue({
    at: "history",
    revision: 1,
    run: {
      run_id: "r",
      source: { entity_type: "patrol_run", entity_id: "p" },
      projection_revision: 1,
      status: "running",
      public_summary: "Patrol at cut",
      completeness,
    },
    steps: [step],
    approvals: [],
    artifacts: [],
    next_cursor: null,
  });
  mocks.getStep.mockResolvedValue(step);
  mocks.getEvents.mockResolvedValue({ events: [], next_cursor: null });
  mocks.streamEvents.mockImplementation(() => new Promise(() => {}));
  let resolve!: (value: unknown) => void;
  mocks.readContent.mockImplementation(() => new Promise((r) => (resolve = r)));
  mocks.getTimeline.mockImplementation(() => new Promise(() => {}));
  const r = await renderComponent(<RunPageClient runId="r" />);
  await act(async () =>
    Array.from(document.querySelectorAll("button"))
      .find((b) => b.textContent === "loadOutput")!
      .click(),
  );
  expect(mocks.readContent.mock.calls[0][2]).toMatchObject({
    at: "history",
    content_kind: "output",
    limit_bytes: 65536,
  });
  await act(async () =>
    Array.from(document.querySelectorAll("button"))
      .find((b) => b.textContent === "nextEvent")!
      .click(),
  );
  await act(async () =>
    resolve({
      availability: "available",
      at: "history",
      content: "Late model text",
      content_type: "text/plain",
      redacted: false,
      truncated: false,
    }),
  );
  expect(document.body.textContent).not.toContain("Late model text");
  expect(document.body.textContent).not.toContain("Public model result");
  await r.unmount();
});
