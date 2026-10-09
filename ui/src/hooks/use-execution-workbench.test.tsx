// @vitest-environment jsdom
import { act, useEffect } from "react";
import { flushSync } from "react-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { DetailPanel } from "@/components/execution/detail-panel";
import { PlaybackControls } from "@/components/execution/playback-controls";

import { ApiError } from "@/lib/api/fetch";
import type { StepDetail, ViewPage } from "@/lib/api/types/execution-view";

import { renderComponent } from "@/test-utils/render";
const mocks = vi.hoisted(() => ({
  controls: false,
  sourceDetail: false,
  readSource: vi.fn(),
  auth: { loading: false, user: { id: "u1" } as { id: string } | null },
  scope: {
    scope: { userId: "u1", workspaceId: "team1" } as { userId: string; workspaceId: string } | null,
    scopeRevision: 1,
  },
  search: "run=r&at=opaque%2B%2F&step=attempt%2F2",
  getTimeline: vi.fn(),
  getEvents: vi.fn(),
  streamEvents: vi.fn(),
  getView: vi.fn(),
  getStep: vi.fn(),
  listSteps: vi.fn(),
  push: vi.fn(),
  replace: vi.fn(),
}));
vi.mock("next-intl", () => ({ useTranslations: () => (key: string) => key }));
vi.mock("@/providers/auth-provider", () => ({ useAuth: () => mocks.auth }));
vi.mock("@/providers/client-data-provider", () => ({ useClientDataScope: () => mocks.scope }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(mocks.search),
  usePathname: () => "/sessions/s",
  useRouter: () => ({ push: mocks.push, replace: mocks.replace }),
}));
vi.mock("@/lib/api/execution-view", () => ({
  executionViewApi: {
    readSource: mocks.readSource,
    getTimeline: mocks.getTimeline,
    getEvents: mocks.getEvents,
    streamEvents: mocks.streamEvents,
    getView: mocks.getView,
    getStep: mocks.getStep,
    listSteps: mocks.listSteps,
  },
}));
import { useExecutionWorkbench } from "./use-execution-workbench";
let current: ReturnType<typeof useExecutionWorkbench>;
function Probe() {
  const value = useExecutionWorkbench();
  useEffect(() => {
    current = value;
  });
  return (
    <div>
      {value.view?.run.public_summary ?? "empty"}|{value.loadState}|
      {value.detail?.step_id ?? "none"}|{value.latestAvailable}
      {mocks.sourceDetail && (
        <DetailPanel
          selection={value.selection}
          run={value.view?.run ?? null}
          page={value.view}
          detail={value.detail}
          onReturnLive={value.returnToLive}
          onChanged={value.refresh}
          onRevoked={value.revokeContent}
          onSelectionChange={value.setSelection}
        />
      )}
      {mocks.controls && (
        <PlaybackControls
          at={value.selection.at}
          latestAvailable={value.latestAvailable}
          start={value.playbackRun?.admitted_at}
          currentTime={value.playbackRun?.as_of}
          coverage={value.playbackRun?.completeness}
          onSeekTime={value.seekTime}
          onSeekEvent={value.seekEvent}
          onReturnLive={value.returnToLive}
        />
      )}
    </div>
  );
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function page(label: string, at = "opaque+/", revision = 1): ViewPage {
  return {
    at,
    revision,
    hidden_count: 0,
    next_cursor: null,
    steps: [],
    run: {
      run_id: "r",
      public_summary: label,
      latest_available: `latest-${label}`,
      projection_revision: revision,
      as_of: null,
      capabilities: [],
      completeness: { state: "complete", missing_fields: [], missing_intervals: [] },
      family: "agent",
      purpose: "production",
      schema_version: 1,
      scope: { owner_user_id: "u1" },
      source: null,
      status: "running",
      wait_reason: null,
    },
  };
}
function step(at = "opaque+/", revision = 1, id = "attempt/2"): StepDetail {
  return { run_id: "r", at, projection_revision: revision, step_id: id } as StepDetail;
}
async function rerender(root: Awaited<ReturnType<typeof renderComponent>>["root"]) {
  await act(async () => {
    root.render(<Probe />);
  });
}
beforeEach(() => {
  window.localStorage?.clear();
  mocks.controls = false;
  mocks.sourceDetail = false;
  mocks.auth = { loading: false, user: { id: "u1" } };
  mocks.scope = { scope: { userId: "u1", workspaceId: "team1" }, scopeRevision: 1 };
  mocks.search = "run=r&at=opaque%2B%2F&step=attempt%2F2";
  mocks.getTimeline.mockResolvedValue({
    run_id: "r",
    revision: 1,
    at: null,
    buckets: [],
    key_events: [],
    latest_available: "2026-01-01T00:01:00Z",
    completeness: { state: "complete", missing_fields: [], missing_intervals: [] },
  });
  mocks.getEvents.mockResolvedValue({
    events: [],
    has_earlier: false,
    prev_cursor: null,
    next_cursor: null,
  });
  mocks.streamEvents.mockImplementation(() => new Promise(() => {}));
  mocks.getView.mockResolvedValue(page("A"));
  mocks.getStep.mockResolvedValue(step());
});
afterEach(() => {
  vi.clearAllMocks();
  document.body.replaceChildren();
});
test("commits view and exact-cut detail together and ignores late selection response", async () => {
  const a = deferred<ViewPage>();
  mocks.getView.mockReturnValueOnce(a.promise);
  const { root, container, unmount } = await renderComponent(<Probe />);
  mocks.search = "run=r&at=B&step=attempt%2F2";
  mocks.getView.mockResolvedValue(page("B", "B", 2));
  mocks.getStep.mockResolvedValue(step("B", 2));
  await rerender(root);
  expect(container.textContent).toBe("B|ready|attempt/2|latest-B");
  await act(async () => a.resolve(page("A")));
  expect(container.textContent).toBe("B|ready|attempt/2|latest-B");
  expect(mocks.getStep.mock.calls.at(-1)?.[2]).toEqual({ at: "B" });
  await unmount();
});
test.each(["workspace", "revision", "logout", "replacement", "loading"])(
  "clears and fences responses on %s change",
  async (change) => {
    const { root, container, unmount } = await renderComponent(<Probe />);
    const late = deferred<ViewPage>();
    mocks.getView.mockReturnValue(late.promise);
    await act(async () => current.refresh());
    if (change === "workspace") mocks.scope.scope!.workspaceId = "team2";
    if (change === "revision") mocks.scope.scopeRevision++;
    if (change === "logout") mocks.auth.user = null;
    if (change === "replacement") mocks.auth.user = { id: "u2" };
    if (change === "loading") mocks.auth.loading = true;
    await rerender(root);
    expect(container.textContent).not.toContain("A|");
    await act(async () => late.resolve(page("old")));
    if (change === "logout" || change === "replacement" || change === "loading")
      expect(container.textContent).toContain("empty|");
    else expect(container.textContent).not.toContain("A|");
    await unmount();
  },
);
test("rejects mismatched detail revision and never shows mixed boundary", async () => {
  mocks.getStep.mockResolvedValue(step("opaque+/", 0));
  const { container, unmount } = await renderComponent(<Probe />);
  expect(container.textContent).toContain("empty|conflict|none");
  expect(current.selection.at).toBe("opaque+/");
  await unmount();
});
test("clears current permission denial, retains confirmed page on rebuilding", async () => {
  const { container, unmount } = await renderComponent(<Probe />);
  mocks.getView.mockRejectedValue(
    new ApiError(503, "rebuilding", { code: "projection_rebuilding" }),
  );
  await act(async () => current.refresh());
  expect(container.textContent).toContain("A|rebuilding");
  mocks.getView.mockRejectedValue(new ApiError(403, "denied", { code: "permission_denied" }));
  await act(async () => current.refresh());
  expect(container.textContent).toContain("empty|forbidden|none");
  await unmount();
});
test("URL navigation drives back/forward, live and view switch preserve exact target", async () => {
  const { root, unmount } = await renderComponent(<Probe />);
  await act(async () => current.setSelection({ ...current.selection, view: "debug" }));
  const url = mocks.push.mock.calls.at(-1)![0] as string;
  expect(new URL(url, "https://x").searchParams.get("at")).toBe("opaque+/");
  mocks.search = url.split("?")[1];
  await rerender(root);
  expect(current.selection.view).toBe("debug");
  mocks.search = "run=r&view=task&at=opaque%2B%2F&step=attempt%2F2";
  await rerender(root);
  expect(current.selection.view).toBe("task");
  mocks.search = url.split("?")[1];
  await rerender(root);
  expect(current.selection.view).toBe("debug");
  await act(async () => current.returnToLive());
  expect(new URL(mocks.push.mock.calls.at(-1)![0], "https://x").searchParams.has("at")).toBe(false);
  await unmount();
});
test("adopts server-authorized replacement and clears not-yet-produced detail", async () => {
  mocks.getStep.mockResolvedValue(step("opaque+/", 1, "actual-attempt"));
  const { container, unmount } = await renderComponent(<Probe />);
  expect(current.selection.stepId).toBe("actual-attempt");
  expect(container.textContent).toContain("actual-attempt");
  mocks.getStep.mockRejectedValue(new ApiError(404, "missing"));
  await act(async () => current.refresh());
  expect(current.selection.stepId).toBe(null);
  expect(current.selection.at).toBe("opaque+/");
  expect(container.textContent).toContain("none");
  await unmount();
});
test("stale permission errors and late detail cannot erase newer view", async () => {
  const oldDetail = deferred<StepDetail>();
  mocks.getStep.mockReturnValueOnce(oldDetail.promise);
  const { root, container, unmount } = await renderComponent(<Probe />);
  mocks.search = "run=r&at=B";
  mocks.getView.mockResolvedValue(page("B", "B", 2));
  await rerender(root);
  await act(async () => oldDetail.reject(new ApiError(403, "denied")));
  expect(container.textContent).toBe("B|ready|none|latest-B");
  await unmount();
});
test("artifact and citation selection cannot carry future objects across cuts", async () => {
  mocks.search = "run=r&at=opaque%2B%2F&artifact=future&version=2&panel=artifact";
  const { root, unmount } = await renderComponent(<Probe />);
  expect(current.selection.artifactId).toBe(null);
  expect(current.targetUnavailable).toBe(true);
  mocks.search = "run=r&at=opaque%2B%2F&step=attempt%2F2&citation=future&panel=source";
  await rerender(root);
  expect(current.selection.citationId).toBe(null);
  expect(current.selection.stepId).toBe("attempt/2");
  await unmount();
});

test("workspace replacement aborts old request and fences an already-resolved continuation", async () => {
  const old = deferred<ViewPage>();
  const next = deferred<ViewPage>();
  mocks.search = "run=r&at=opaque%2B%2F";
  mocks.getView.mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise);
  const { root, container, unmount } = await renderComponent(<Probe />);
  const oldSignal = mocks.getView.mock.calls[0][2].signal as AbortSignal;
  mocks.scope.scope = { userId: "u1", workspaceId: "team2" };
  await rerender(root);
  await act(async () => old.resolve(page("old")));
  expect(oldSignal.aborted).toBe(true);
  expect(container.textContent).toContain("empty|loading");
  expect(mocks.getView.mock.calls[1][2].workspaceId).toBe("team2");
  await act(async () => next.resolve(page("new")));
  expect(container.textContent).toContain("new|ready");
  await unmount();
});
test("typed resource unavailability remains distinct from revision conflict", async () => {
  mocks.getView.mockRejectedValue(
    new ApiError(409, "not localized", { code: "resource_unavailable" }),
  );
  const { container, unmount } = await renderComponent(<Probe />);
  expect(container.textContent).toContain("empty|unavailable");
  expect(current.selection.at).toBe("opaque+/");
  await unmount();
});
test("canonical replacement preserves query edits that happened during loading", async () => {
  const late = deferred<StepDetail>();
  mocks.getStep.mockReturnValue(late.promise);
  mocks.search += "&init=hello";
  const { root, unmount } = await renderComponent(<Probe />);
  mocks.search = "run=r&at=opaque%2B%2F&step=attempt%2F2&unrelated=new";
  await rerender(root);
  await act(async () => late.resolve(step("opaque+/", 1, "replacement")));
  const query = new URL(mocks.replace.mock.calls.at(-1)![0], "https://x").searchParams;
  expect(query.has("init")).toBe(false);
  expect(query.get("unrelated")).toBe("new");
  await unmount();
});

test("an artifact on another attempt cannot satisfy the selected attempt relationship", async () => {
  mocks.search = "run=r&at=opaque%2B%2F&step=attempt%2F2&artifact=other&version=2";
  mocks.getView.mockResolvedValue({
    ...page("A"),
    artifacts: [{ artifact_id: "other", version: 2, availability: "available" }],
  });
  const { unmount } = await renderComponent(<Probe />);
  expect(current.selection.stepId).toBe("attempt/2");
  expect(current.selection.artifactId).toBe(null);
  await unmount();
});
test("view preference is scoped and explicit URL view overrides it", async () => {
  const saved = new Map<string, string>();
  const original = Object.getOwnPropertyDescriptor(window, "localStorage");
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => saved.get(key) ?? null,
      setItem: (key: string, value: string) => saved.set(key, value),
    },
  });
  mocks.search = "run=r";
  const { root, unmount } = await renderComponent(<Probe />);
  await act(async () => current.setLayout({ view: "debug", detailWidth: 450 }));
  expect(current.selection.view).toBe("debug");
  const preferenceKey = [...saved.keys()][0];
  saved.set(
    preferenceKey,
    JSON.stringify({ ...JSON.parse(saved.get(preferenceKey)!), contextCollapsed: true }),
  );
  await act(async () => current.setLayout({ ...current.layout, conversationOpen: false }));
  expect(JSON.parse(saved.get(preferenceKey)!).contextCollapsed).toBe(true);
  mocks.scope.scope = { userId: "u1", workspaceId: "team2" };
  await rerender(root);
  expect(current.selection.view).toBe("task");
  mocks.scope.scope = { userId: "u1", workspaceId: "team1" };
  mocks.search = "run=r&view=task";
  await rerender(root);
  expect(current.selection.view).toBe("task");
  expect(
    [...saved.values()].every(
      (value) => !value.includes("public_summary") && !value.includes("latest-"),
    ),
  ).toBe(true);
  await unmount();
  if (original) Object.defineProperty(window, "localStorage", original);
  else Reflect.deleteProperty(window, "localStorage");
});
test("changing detail keeps confirmed main page and clears the old detail immediately", async () => {
  const { root, container, unmount } = await renderComponent(<Probe />);
  const pending = deferred<ViewPage>();
  mocks.getView.mockReturnValue(pending.promise);
  mocks.search = "run=r&at=opaque%2B%2F&step=another-attempt&view=debug";
  await rerender(root);
  expect(container.textContent).toBe("A|refreshing|none|latest-A");
  expect(current.selection.stepId).toBe("another-attempt");
  await unmount();
});
test("paged-out citation remains selected only after an exact-cut page confirms it", async () => {
  mocks.search = "run=r&at=opaque%2B%2F&citation=c";
  mocks.getView.mockResolvedValue({ ...page("A"), next_cursor: "opaque-event-page+/" });
  mocks.listSteps.mockResolvedValue({
    at: "opaque+/",
    revision: 1,
    next_cursor: null,
    items: [{ citation_refs: [{ citation_id: "c", availability: "available" }] }],
  });
  const { unmount } = await renderComponent(<Probe />);
  expect(current.selection.citationId).toBe("c");
  expect(current.loadState).toBe("ready");
  expect(mocks.listSteps.mock.calls[0][1]).toEqual({
    at: "opaque+/",
    revision: 1,
    cursor: "opaque-event-page+/",
  });
  await unmount();
});
test("a mismatched continuation cannot authorize a future citation", async () => {
  mocks.search = "run=r&at=opaque%2B%2F&citation=c";
  mocks.getView.mockResolvedValue({ ...page("A"), next_cursor: "opaque-page" });
  mocks.listSteps.mockResolvedValue({
    at: "future",
    revision: 2,
    next_cursor: null,
    items: [{ citation_refs: [{ citation_id: "c" }] }],
  });
  const { unmount } = await renderComponent(<Probe />);
  expect(current.loadState).toBe("conflict");
  expect(current.view).toBe(null);
  await unmount();
});
test("incomplete history never claims an absent artifact was not produced", async () => {
  mocks.search = "run=r&at=opaque%2B%2F&artifact=missing&version=2";
  const value = page("A");
  value.run.completeness.state = "partial";
  mocks.getView.mockResolvedValue(value);
  const { unmount } = await renderComponent(<Probe />);
  expect(current.selection.artifactId).toBe("missing");
  expect(current.targetUnavailable).toBe(true);
  await unmount();
});
test("logout fences a promise already resolved before its continuation executes", async () => {
  const late = deferred<StepDetail>();
  mocks.getStep.mockReturnValue(late.promise);
  const { root, container, unmount } = await renderComponent(<Probe />);
  await act(async () => {
    late.resolve(step());
    mocks.auth.user = null;
    flushSync(() => root.render(<Probe />));
    await Promise.resolve();
  });
  expect(container.textContent).toBe("empty|idle|none|");
  expect(current.error).toBe(null);
  await unmount();
});
test("return to live passes null and a late historical detail cannot replace it", async () => {
  const history = deferred<StepDetail>();
  mocks.getStep.mockReturnValueOnce(history.promise);
  const { root, container, unmount } = await renderComponent(<Probe />);
  await act(async () => current.returnToLive());
  mocks.search = new URL(mocks.push.mock.calls.at(-1)![0], "https://x").search.slice(1);
  mocks.getView.mockResolvedValue(page("live", "new-live-cut", 2));
  mocks.getStep.mockResolvedValue(step("new-live-cut", 2));
  await rerender(root);
  expect(mocks.getView.mock.calls.at(-1)![1]).toEqual({ at: null });
  expect(current.selection.at).toBe(null);
  await act(async () => history.resolve(step()));
  expect(container.textContent).toBe("live|ready|attempt/2|latest-live");
  await unmount();
});
test("missing URL run remains idle without inventing a session-to-run identity", async () => {
  mocks.search = "init=hello";
  const { container, unmount } = await renderComponent(<Probe />);
  expect(container.textContent).toBe("empty|idle|none|");
  expect(mocks.getView).not.toHaveBeenCalled();
  await unmount();
});

test("missing target notice survives canonical replace feedback until user navigation", async () => {
  mocks.search = "run=r&at=opaque%2B%2F&artifact=future&version=1";
  mocks.replace.mockImplementation((url: string) => {
    mocks.search = url.split("?")[1];
  });
  const { root, unmount } = await renderComponent(<Probe />);
  expect(current.targetUnavailable).toBe(true);
  await rerender(root);
  expect(current.selection.artifactId).toBeNull();
  expect(current.targetUnavailable).toBe(true);
  mocks.search = "run=r&at=opaque%2B%2F&step=attempt%2F2";
  await rerender(root);
  expect(current.targetUnavailable).toBe(false);
  await unmount();
  mocks.replace.mockReset();
});
test("missing notice acknowledgement and identity transitions cannot resurrect it", async () => {
  mocks.search = "run=r&at=opaque%2B%2F&artifact=future&version=1";
  mocks.replace.mockImplementation((url: string) => {
    mocks.search = url.split("?")[1];
  });
  const result = await renderComponent(<Probe />);
  await act(async () => current.dismissTargetNotice());
  expect(current.targetUnavailable).toBe(false);
  await rerender(result.root);
  expect(current.targetUnavailable).toBe(false);
  mocks.search = "run=r&at=opaque%2B%2F&artifact=future&version=1";
  await rerender(result.root);
  await rerender(result.root);
  expect(current.targetUnavailable).toBe(true);
  mocks.auth.user = null;
  await rerender(result.root);
  expect(current.targetUnavailable).toBe(false);
  mocks.auth.user = { id: "u1" };
  await rerender(result.root);
  expect(current.targetUnavailable).toBe(false);
  await result.unmount();
  mocks.replace.mockReset();
});
test("Debug progressively loads trace under the coordinator fence and clears on revocation", async () => {
  mocks.search = "run=r&view=debug";
  const first = page("trace");
  first.next_cursor = "tail";
  first.steps = [{ ...step(), step_id: "one" }];
  mocks.getView.mockResolvedValue(first);
  const tail = deferred<import("@/lib/api/types/execution-view").StepViewPage>();
  mocks.listSteps.mockReturnValue(tail.promise);
  const rendered = await renderComponent(<Probe />);
  expect(current.trace?.steps.map((s) => s.step_id)).toEqual(["one"]);
  expect(current.trace?.exhausted).toBe(false);
  await act(async () => tail.reject(new ApiError(403, "forbidden")));
  expect(current.view).toBeNull();
  expect(current.trace).toBeNull();
  await rendered.unmount();
});
test("Debug scope revision invalidates a delayed continuation", async () => {
  mocks.search = "run=r&view=debug";
  const first = page("trace");
  first.next_cursor = "tail";
  mocks.getView.mockResolvedValue(first);
  const tail = deferred<import("@/lib/api/types/execution-view").StepViewPage>();
  mocks.listSteps.mockReturnValue(tail.promise);
  const rendered = await renderComponent(<Probe />);
  mocks.scope.scopeRevision++;
  mocks.getView.mockResolvedValue(page("new"));
  await rerender(rendered.root);
  await act(async () =>
    tail.resolve({
      at: first.at!,
      revision: 1,
      items: [{ ...step(), step_id: "stale" }],
      next_cursor: null,
      hidden_count: 0,
      completeness: { state: "complete", missing_fields: [], missing_intervals: [] },
    }),
  );
  expect(current.trace?.steps).toEqual([]);
  await rendered.unmount();
});

test("seek intent masks old body and trace before URL navigation, debounces and release flushes latest target", async () => {
  vi.useFakeTimers();
  const { root, container } = await renderComponent(<Probe />);
  expect(container.textContent).toContain("A");
  const slow = deferred<unknown>();
  mocks.getTimeline.mockReturnValueOnce(slow.promise);
  await act(async () => {
    current.seekTime("2026-01-01T00:00:10Z");
  });
  expect(current.view).toBeNull();
  expect(current.detail).toBeNull();
  expect(current.trace).toBeNull();
  await act(async () => {
    vi.advanceTimersByTime(149);
  });
  expect(mocks.getTimeline).not.toHaveBeenCalled();
  await act(async () => {
    vi.advanceTimersByTime(1);
  });
  const fast = deferred<unknown>();
  mocks.getTimeline.mockReturnValueOnce(fast.promise);
  await act(async () => {
    current.seekTime("2026-01-01T00:00:20Z", true);
  });
  await act(async () => {
    fast.resolve({
      run_id: "r",
      at: "new-cut",
      latest_available: "latest",
      completeness: { state: "complete", missing_fields: [], missing_intervals: [] },
    });
  });
  expect(mocks.push).toHaveBeenLastCalledWith(expect.stringContaining("at=new-cut"), {
    scroll: false,
  });
  await act(async () => {
    slow.resolve({ run_id: "r", at: "old-cut" });
  });
  expect(mocks.push).toHaveBeenCalledTimes(1);
  await act(async () => root.unmount());
  vi.useRealTimers();
});

test("historical event updates only authoritative watermark and revocation clears all bodies", async () => {
  vi.useFakeTimers();
  const history = page("history");
  history.approvals = [{ approval_id: "approval", status: "pending" }];
  history.artifacts = [];
  history.messages = [{ message_id: "past", public_summary: "Recorded summary" }];
  mocks.getView.mockResolvedValue(history);
  const { root } = await renderComponent(<Probe />);
  await act(async () => {
    vi.advanceTimersByTime(250);
  });
  const initial = current.view;
  const reads = mocks.getView.mock.calls.length;
  const subscription = mocks.streamEvents.mock.calls.at(-1);
  expect(subscription).toBeDefined();
  mocks.getTimeline.mockResolvedValue({
    run_id: "r",
    latest_available: "2026-01-01T00:05:00Z",
    at: null,
  });
  await act(async () => {
    subscription![1]({
      event_id: "new-artifact",
      cursor: "feed-not-view",
      payload: {
        artifact_id: "future",
        approval_id: "approval",
        status: "approved",
        public_summary: "Future full text",
      },
    });
    vi.advanceTimersByTime(250);
  });
  expect(current.view).toBe(initial);
  expect(current.view?.approvals?.[0].status).toBe("pending");
  expect(current.view?.artifacts).toEqual([]);
  expect(current.view?.messages).toEqual([
    { message_id: "past", public_summary: "Recorded summary" },
  ]);
  expect(current.selection.at).toBe("opaque+/");
  expect(current.latestAvailable).toBe("2026-01-01T00:05:00Z");
  expect(mocks.getView).toHaveBeenCalledTimes(reads);
  expect(mocks.push).not.toHaveBeenCalled();
  await act(async () => subscription![2].onRefresh({ code: "permission_denied" }));
  expect(current.view).toBeNull();
  expect(current.detail).toBeNull();
  expect(current.trace).toBeNull();
  expect(current.loadState).toBe("forbidden");
  await act(async () => root.unmount());
  vi.useRealTimers();
});

test("live invalidations wait for progressive trace to finish before coalesced refresh", async () => {
  vi.useFakeTimers();
  mocks.search = "run=r&view=debug";
  const first = { ...page("live"), next_cursor: "next" };
  mocks.getView.mockResolvedValue(first);
  const trace = deferred<unknown>();
  mocks.listSteps.mockReturnValue(trace.promise);
  const { root } = await renderComponent(<Probe />);
  const receive = mocks.streamEvents.mock.calls.at(-1)![1];
  await act(async () => {
    for (let n = 0; n < 20; n++) receive({ event_id: String(n), cursor: `feed-${n}` });
    vi.advanceTimersByTime(1000);
  });
  expect(mocks.getView).toHaveBeenCalledTimes(1);
  await act(async () =>
    trace.resolve({
      at: first.at,
      revision: first.revision,
      items: [],
      next_cursor: null,
      completeness: first.run.completeness,
    }),
  );
  mocks.getView.mockResolvedValue({ ...first, next_cursor: null });
  await act(async () => {
    vi.advanceTimersByTime(250);
  });
  expect(mocks.getView).toHaveBeenCalledTimes(2);
  expect(current.trace?.exhausted).toBe(true);
  await act(async () => root.unmount());
  vi.useRealTimers();
});
test("old scope watermark and stream callbacks are fenced after scope revision changes", async () => {
  vi.useFakeTimers();
  const { root } = await renderComponent(<Probe />);
  const slow = deferred<unknown>();
  mocks.getTimeline.mockReturnValueOnce(slow.promise);
  await act(async () => {
    vi.advanceTimersByTime(250);
  });
  const old = mocks.streamEvents.mock.calls.at(-1)!;
  mocks.scope = { ...mocks.scope, scopeRevision: 9 };
  await rerender(root);
  await act(async () => {
    slow.resolve({ run_id: "r", latest_available: "leak" });
    old[1]({ event_id: "late", cursor: "late" });
    old[2].onRefresh({ code: "permission_denied" });
  });
  expect(current.latestAvailable).not.toBe("leak");
  expect(current.loadState).toBe("ready");
  await act(async () => root.unmount());
  vi.useRealTimers();
});
test("known gap and no neighboring event retain the historical at without exposing old body", async () => {
  const withGap = page("gap");
  withGap.run.completeness = {
    state: "partial",
    missing_fields: [],
    missing_intervals: [
      { start: "2026-01-01T00:00:00Z", end: "2026-01-01T00:00:20Z", reason: "retained" },
    ],
  };
  mocks.getView.mockResolvedValue(withGap);
  const { root } = await renderComponent(<Probe />);
  await act(async () => current.seekTime("2026-01-01T00:00:10Z", true));
  expect(current.loadState).toBe("unavailable");
  expect(current.view).toBeNull();
  expect(mocks.getTimeline).not.toHaveBeenCalled();
  await act(async () => current.seekEvent("before"));
  expect(mocks.getTimeline).toHaveBeenLastCalledWith(
    "r",
    expect.objectContaining({ anchor_at: "opaque+/", direction: "before" }),
    expect.anything(),
  );
  expect(current.selection.at).toBe("opaque+/");
  expect(mocks.push).not.toHaveBeenCalled();
  await act(async () => root.unmount());
});

test("a forbidden view read stops automatic feed refresh until explicit retry", async () => {
  vi.useFakeTimers();
  mocks.search = "run=r";
  mocks.getView.mockRejectedValue(new ApiError(403, "permission_denied"));
  const { root } = await renderComponent(<Probe />);
  await act(async () => {
    vi.advanceTimersByTime(2000);
  });
  expect(mocks.getView).toHaveBeenCalledTimes(1);
  expect(current.loadState).toBe("forbidden");
  await act(async () => root.unmount());
  vi.useRealTimers();
});

test("seek authorization failure immediately hides metadata as forbidden", async () => {
  const { root } = await renderComponent(<Probe />);
  mocks.getTimeline.mockRejectedValueOnce(new ApiError(403, "permission_denied"));
  await act(async () => current.seekEvent("before"));
  expect(current.loadState).toBe("forbidden");
  expect(current.playbackRun).toBeNull();
  expect(current.latestAvailable).toBeNull();
  await act(async () => root.unmount());
});

test("retired timeline anchor reports conflict without discarding historical location", async () => {
  const { root } = await renderComponent(<Probe />);
  mocks.getTimeline.mockRejectedValueOnce(new ApiError(409, "revision_conflict"));
  await act(async () => current.seekEvent("after"));
  expect(current.loadState).toBe("conflict");
  expect(current.selection.at).toBe("opaque+/");
  expect(current.view).toBeNull();
  await act(async () => root.unmount());
});

test("explicit retry recovers masked seek failure at the retained location", async () => {
  const { root } = await renderComponent(<Probe />);
  mocks.getTimeline.mockRejectedValueOnce(new ApiError(503, "rebuilding"));
  await act(async () => current.seekEvent("before"));
  expect(current.loadState).toBe("rebuilding");
  await act(async () => current.refresh());
  expect(current.loadState).toBe("ready");
  expect(current.view?.at).toBe("opaque+/");
  await act(async () => root.unmount());
});

test("a newer explicit selection cancels a pending seek before URL navigation", async () => {
  const { root } = await renderComponent(<Probe />);
  const slow = deferred<unknown>();
  mocks.getTimeline.mockReturnValueOnce(slow.promise);
  await act(async () => current.seekTime("2026-01-01T00:00:10Z", true));
  await act(async () => current.setSelection({ ...current.selection, stepId: "new-step" }));
  await act(async () => slow.resolve({ run_id: "r", at: "stale-seek" }));
  expect(mocks.push).toHaveBeenCalledTimes(1);
  expect(mocks.push.mock.calls[0][0]).toContain("step=new-step");
  await act(async () => root.unmount());
});

test.each(["stream", "seek"])(
  "%s denial cannot revive revoked bodies during retry or after 503",
  async (source) => {
    mocks.search += "&view=debug";
    const { root } = await renderComponent(<Probe />);
    expect(current.view).not.toBeNull();
    expect(current.detail).not.toBeNull();
    expect(current.trace).not.toBeNull();
    if (source === "stream")
      await act(async () =>
        mocks.streamEvents.mock.calls.at(-1)![2].onRefresh({ code: "permission_denied" }),
      );
    else {
      mocks.getTimeline.mockRejectedValueOnce(new ApiError(403, "permission_denied"));
      await act(async () => current.seekEvent("before"));
    }
    const retry = deferred<ViewPage>();
    mocks.getView.mockReturnValueOnce(retry.promise);
    await act(async () => current.refresh());
    expect(current.view).toBeNull();
    expect(current.detail).toBeNull();
    expect(current.trace).toBeNull();
    expect(current.playbackRun).toBeNull();
    expect(current.latestAvailable).toBeNull();
    await act(async () => retry.reject(new ApiError(503, "rebuilding")));
    expect(current.view).toBeNull();
    expect(current.detail).toBeNull();
    expect(current.trace).toBeNull();
    expect(current.playbackRun).toBeNull();
    expect(current.latestAvailable).toBeNull();
    await act(async () => current.refresh());
    expect(current.view?.run.public_summary).toBe("A");
    await act(async () => root.unmount());
  },
);

test.each([
  {
    state: "partial",
    missing_fields: [],
    missing_intervals: [
      { start: "2026-01-01T00:00:00Z", end: "2026-01-01T00:00:20Z", reason: "retained" },
    ],
  },
  {
    state: "partial",
    missing_fields: [],
    missing_intervals: [{ start: null, end: null, reason: "retained" }],
  },
  { state: "unavailable", missing_fields: [], missing_intervals: [] },
  { state: "rebuilding", missing_fields: [], missing_intervals: [] },
  undefined,
])(
  "new authoritative timeline coverage rejects target despite non-null at: %j",
  async (completeness) => {
    const { root } = await renderComponent(<Probe />);
    mocks.getTimeline.mockResolvedValueOnce({ run_id: "r", at: "surviving-earlier", completeness });
    await act(async () => current.seekTime("2026-01-01T00:00:10Z", true));
    expect(mocks.push).not.toHaveBeenCalled();
    expect(current.view).toBeNull();
    expect(current.selection.at).toBe("opaque+/");
    await act(async () => root.unmount());
  },
);

test.each([false, true])(
  "real range drag into gap cancels prior timer/request (started=%s)",
  async (started) => {
    vi.useFakeTimers();
    mocks.controls = true;
    const history = page("drag");
    history.run.admitted_at = "2026-01-01T00:00:00Z";
    history.run.as_of = "2026-01-01T00:00:10Z";
    history.run.latest_available = "2026-01-01T00:00:30Z";
    history.run.completeness = {
      state: "partial",
      missing_fields: [],
      missing_intervals: [
        { start: "2026-01-01T00:00:15Z", end: "2026-01-01T00:00:25Z", reason: "retained" },
      ],
    };
    mocks.getView.mockResolvedValue(history);
    const { root, container } = await renderComponent(<Probe />);
    const slow = deferred<unknown>();
    mocks.getTimeline.mockReturnValueOnce(slow.promise);
    const slider = container.querySelector('input[type="range"]') as HTMLInputElement;
    const change = async (time: string) =>
      act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(
          slider,
          String(Date.parse(time)),
        );
        slider.dispatchEvent(new Event("input", { bubbles: true }));
      });
    await change("2026-01-01T00:00:05Z");
    expect(current.seeking).toBe(true);
    if (started)
      await act(async () => {
        vi.advanceTimersByTime(150);
      });
    await change("2026-01-01T00:00:20Z");
    if (started)
      await act(async () =>
        slow.resolve({
          run_id: "r",
          at: "stale-valid-target",
          completeness: { state: "complete", missing_fields: [], missing_intervals: [] },
        }),
      );
    await act(async () => {
      vi.advanceTimersByTime(150);
    });
    expect(mocks.getTimeline).toHaveBeenCalledTimes(started ? 1 : 0);
    expect(mocks.push).not.toHaveBeenCalled();
    expect(current.view).toBeNull();
    expect(current.loadState).toBe("unavailable");
    await act(async () => root.unmount());
    vi.useRealTimers();
  },
);

test("a detail-reader denial destroys the shared exposed snapshot and cannot revive it during retry", async () => {
  const result = await renderComponent(<Probe />);
  expect(current.view).not.toBeNull();
  await act(async () => current.revokeContent());
  expect(current.view).toBeNull();
  expect(current.detail).toBeNull();
  expect(current.trace).toBeNull();
  expect(current.playbackRun).toBeNull();
  mocks.getView.mockImplementation(() => new Promise(() => {}));
  await act(async () => current.refresh());
  expect(current.view).toBeNull();
  expect(current.detail).toBeNull();
  await result.unmount();
});

test("real workbench owner preserves proven locator fallback, then 403 destroys all exposed state", async () => {
  mocks.sourceDetail = true;
  mocks.search = "run=r&at=opaque%2B%2F&step=attempt%2F2&citation=c&panel=source";
  const sourceStep = {
    ...step(),
    kind: "tool",
    status: "completed",
    artifact_refs: [],
    citation_refs: [
      {
        citation_id: "c",
        resource_kind: "knowledge_base",
        knowledge_base_id: "kb",
        version_id: "v1",
        document_revision_id: "r1",
        doc_id: "d",
        chunk_id: "gone",
        page_no: 2,
        availability: "available",
      },
    ],
  } as StepDetail;
  mocks.getView.mockResolvedValue({ ...page("PRIVATE SNAPSHOT"), steps: [sourceStep] });
  mocks.getStep.mockResolvedValue(sourceStep);
  mocks.readSource
    .mockResolvedValueOnce({
      availability: "unavailable",
      reason: "source_locator_unavailable",
      content_type: "text/plain",
      truncated: false,
    })
    .mockResolvedValueOnce({
      availability: "available",
      content: "FIXED PAGE",
      source_title: "Fixed title",
      content_type: "text/plain",
      truncated: true,
      next_cursor: "n",
    })
    .mockRejectedValueOnce(new ApiError(403, "denied"));
  const r = await renderComponent(<Probe />);
  const click = async (text: string) => {
    await act(async () => {
      const button = Array.from(r.container.querySelectorAll("button")).find(
        (button) => button.textContent === text,
      );
      expect(button).toBeDefined();
      button!.click();
    });
  };
  await click("loadSource");
  expect(current.view).not.toBeNull();
  await act(async () => {
    (r.container.querySelector('[data-source-fallback="page"]') as HTMLButtonElement).click();
  });
  await click("loadSource");
  expect(r.container.textContent).toContain("FIXED PAGE");
  expect(mocks.readSource).toHaveBeenLastCalledWith(
    "c",
    expect.objectContaining({ locator: "page" }),
    expect.objectContaining({ workspaceId: "team1" }),
  );
  await click("nextPage");
  expect(current.view).toBeNull();
  expect(current.detail).toBeNull();
  expect(current.trace).toBeNull();
  expect(r.container.textContent).not.toContain("FIXED PAGE");
  expect(r.container.textContent).not.toContain("PRIVATE SNAPSHOT");
  await r.unmount();
});

test("artifact to source and URL back preserve the exact run cut and immutable version", async () => {
  const original = "run=r&at=opaque%2B%2F&artifact=a&version=1&panel=artifact";
  mocks.search = original;
  const sourceStep = {
    ...step(),
    artifact_refs: [{ artifact_id: "a", version: 1, availability: "available" }],
    citation_refs: [{ citation_id: "c", availability: "available" }],
  } as StepDetail;
  mocks.getView.mockResolvedValue({
    ...page("fixed"),
    steps: [sourceStep],
    artifacts: [{ artifact_id: "a", version: 1, availability: "available" }],
  });
  mocks.getStep.mockResolvedValue(sourceStep);
  const r = await renderComponent(<Probe />);
  await act(async () =>
    current.setSelection({
      ...current.selection,
      artifactId: null,
      version: null,
      citationId: "c",
      stepId: "attempt/2",
      panel: "source",
    }),
  );
  mocks.search = "run=r&at=opaque%2B%2F&step=attempt%2F2&citation=c&panel=source";
  await rerender(r.root);
  expect(current.selection.citationId).toBe("c");
  expect(current.selection.at).toBe("opaque+/");
  mocks.search = original;
  await rerender(r.root);
  expect(current.selection).toMatchObject({
    runId: "r",
    at: "opaque+/",
    artifactId: "a",
    version: 1,
    citationId: null,
    panel: "artifact",
  });
  await r.unmount();
});
