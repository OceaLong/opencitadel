// @vitest-environment jsdom
import { expect, test, vi } from "vitest";

import { parseSelection } from "@/lib/execution-view/url-state";

import { renderComponent } from "@/test-utils/render";
const mocks = vi.hoisted(() => ({
  source: { entity_type: "session", entity_id: "s" } as {
    entity_type: string;
    entity_id: string;
    session_id?: string;
  },
  replace: vi.fn(),
  debug: false,
}));
vi.mock("@/providers/auth-provider", () => ({
  useAuth: () => ({ user: { id: "u" }, loading: false }),
}));
vi.mock("@/providers/client-data-provider", () => ({
  useClientDataScope: () => ({ scope: { userId: "u", workspaceId: "w" }, scopeRevision: 1 }),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace }),
  useSearchParams: () => new URLSearchParams("run=r&at=cut&step=one&init=ignore"),
}));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("@/components/markdown-content", () => ({
  MarkdownContent: ({ content }: { content: string }) => <p>{content}</p>,
}));
vi.mock("@/hooks/use-execution-workbench", () => ({
  useExecutionWorkbench: () => ({
    selection: parseSelection(`run=r&at=cut&step=one&view=${mocks.debug ? "debug" : "task"}`)
      .selection,
    setSelection: vi.fn(),
    layout: {},
    setLayout: vi.fn(),
    loadState: "ready",
    view: {
      run: {
        run_id: "r",
        source: mocks.source,
        status: "completed",
        completeness: { state: "complete" },
      },
      steps: [],
      approvals: [],
      artifacts: [],
    },
  }),
}));
import { RunPageClient } from "./run-page-client";
test("session Run route retains exact selection and maps non-session source to read-only shared view", async () => {
  const first = await renderComponent(<RunPageClient runId="r" />);
  expect(mocks.replace.mock.calls[0][0]).toContain("/sessions/s?");
  expect(mocks.replace.mock.calls[0][0]).toContain("at=cut");
  expect(mocks.replace.mock.calls[0][0]).not.toContain("init=");
  await first.unmount();
  mocks.source = { entity_type: "patrol_run", entity_id: "patrol" };
  mocks.replace.mockClear();
  const second = await renderComponent(<RunPageClient runId="r" />);
  expect(mocks.replace).not.toHaveBeenCalled();
  expect(second.container.querySelector("a")?.getAttribute("href")).toBe("/patrol-runs/patrol");
  expect(second.container.querySelector("textarea")).toBeNull();
  await second.unmount();
});
test.each([
  ["resource_build", "/knowledge"],
  ["scheduled_job", "/automation"],
  ["patrol_pack_validation", "/patrols"],
])("non-session %s retains its original module entry", async (entity_type, href) => {
  mocks.source = { entity_type, entity_id: "source-id" };
  mocks.replace.mockClear();
  const result = await renderComponent(<RunPageClient runId="r" />);
  expect(result.container.querySelector("a")?.getAttribute("href")).toBe(href);
  expect(result.container.querySelector("textarea")).toBeNull();
  await result.unmount();
});

test("non-session Debug mounts shared trace", async () => {
  mocks.debug = true;
  mocks.source = { entity_type: "patrol_run", entity_id: "p" };
  const result = await renderComponent(<RunPageClient runId="r" />);
  expect(result.container.querySelector('section[aria-label="title"]')).not.toBeNull();
  await result.unmount();
  mocks.debug = false;
});
