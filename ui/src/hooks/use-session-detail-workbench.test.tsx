// @vitest-environment jsdom
import { act, useEffect } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";
const mocks = vi.hoisted(() => ({
  auth: { loading: false, user: { id: "u1" } as { id: string } | null },
  scope: { scope: { userId: "u1", workspaceId: "t1" }, scopeRevision: 1 },
  search: "run=r&at=cut&view=debug&step=attempt-2&init=hello",
  getView: vi.fn(),
  getStep: vi.fn(),
  artifacts: vi.fn(),
  replace: vi.fn(),
  send: vi.fn(),
}));
vi.mock("@/providers/auth-provider", () => ({ useAuth: () => mocks.auth }));
vi.mock("@/providers/client-data-provider", () => ({ useClientDataScope: () => mocks.scope }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(mocks.search),
  usePathname: () => "/sessions/s",
  useRouter: () => ({ push: vi.fn(), replace: mocks.replace }),
}));
vi.mock("next-intl", () => ({
  useLocale: () => "en",
  useTranslations: () => (key: string) => key,
}));
vi.mock("@/hooks/use-require-auth", () => ({
  useRequireAuth: () => ({ requireAuth: () => true }),
}));
vi.mock("@/lib/api/execution-view", () => ({
  executionViewApi: { getView: mocks.getView, getStep: mocks.getStep },
}));
vi.mock("@/lib/api/artifacts", () => ({ artifactsApi: { listBySession: mocks.artifacts } }));
vi.mock("@/hooks/use-session-detail", () => ({
  useSessionDetail: () => ({
    session: { status: "waiting" },
    files: [],
    events: [],
    loading: false,
    streaming: false,
    sendMessage: mocks.send,
  }),
}));
import { useSessionDetailView } from "./use-session-detail-view";
let current: ReturnType<typeof useSessionDetailView>;
function Probe({ initialMessage }: { initialMessage?: string }) {
  const value = useSessionDetailView({ sessionId: "s", initialMessage });
  useEffect(() => {
    current = value;
  });
  return (
    <div>
      {value.workbench?.view?.run.public_summary ?? "empty"}|
      {value.sessionArtifacts.map((item) => item.title).join(",")}
    </div>
  );
}
beforeEach(() => {
  mocks.auth = { loading: false, user: { id: "u1" } };
  mocks.scope = { scope: { userId: "u1", workspaceId: "t1" }, scopeRevision: 1 };
  mocks.getView.mockResolvedValue({
    at: "cut",
    revision: 1,
    next_cursor: null,
    steps: [],
    run: { run_id: "r", projection_revision: 1, public_summary: "actual F08" },
  });
  mocks.getStep.mockResolvedValue({
    at: "cut",
    projection_revision: 1,
    run_id: "r",
    step_id: "attempt-2",
  });
  mocks.artifacts.mockResolvedValue({ artifacts: [] });
  mocks.send.mockResolvedValue(undefined);
});
afterEach(() => {
  vi.clearAllMocks();
  vi.useRealTimers();
  document.body.replaceChildren();
});
test("session composition actually loads F08 exact run and detail", async () => {
  const { container, unmount } = await renderComponent(<Probe />);
  expect(container.textContent).toContain("actual F08");
  expect(current.workbench.selection).toMatchObject({
    runId: "r",
    at: "cut",
    stepId: "attempt-2",
    view: "debug",
  });
  await unmount();
});
test("bootstrap cleanup preserves live navigation keys", async () => {
  vi.useFakeTimers();
  window.history.replaceState(
    null,
    "",
    "/sessions/s?init=hello&run=r&at=cut&step=attempt-2#anchor",
  );
  const { unmount } = await renderComponent(<Probe initialMessage="hello" />);
  await act(async () => {
    vi.advanceTimersByTime(100);
  });
  expect(
    mocks.replace.mock.calls.some(
      (call) => call[0] === "/sessions/s?run=r&at=cut&step=attempt-2#anchor",
    ),
  ).toBe(true);
  await unmount();
});
test("legacy artifact preview clears on identity change and discards late results", async () => {
  mocks.artifacts.mockResolvedValueOnce({
    artifacts: [{ id: "a", title: "u1 content", version_refs: [] }],
  });
  const { root, container, unmount } = await renderComponent(<Probe />);
  expect(container.textContent).toContain("u1 content");
  let resolve!: (value: unknown) => void;
  mocks.artifacts.mockReturnValue(
    new Promise((r) => {
      resolve = r;
    }),
  );
  mocks.scope.scope.workspaceId = "t2";
  await act(async () => {
    root.render(<Probe />);
  });
  expect(container.textContent).not.toContain("u1 content");
  mocks.auth.user = null;
  await act(async () => {
    root.render(<Probe />);
  });
  await act(async () =>
    resolve({ artifacts: [{ id: "b", title: "late content", version_refs: [] }] }),
  );
  expect(container.textContent).toBe("empty|");
  await unmount();
});
