// @vitest-environment jsdom
import { act, useEffect, useState } from "react";
import { afterEach, expect, test, vi } from "vitest";

import { useExecutionWorkbench } from "@/hooks/use-execution-workbench";
import { ApiError } from "@/lib/api/fetch";
import type { WorkbenchSelection } from "@/lib/execution-view/state";
import { parseSelection } from "@/lib/execution-view/url-state";

import { renderComponent } from "@/test-utils/render";
const mocks = vi.hoisted(() => ({
  real: false,
  search: "run=r",
  viewApi: vi.fn(),
  stepApi: vi.fn(),
  bodyApi: vi.fn(),
  timelineApi: vi.fn(),
  eventsApi: vi.fn(),
  streamApi: vi.fn(),
  push: vi.fn(),
  select: vi.fn(),
  scope: { userId: "u", workspaceId: "team" },
  send: vi.fn(),
  ask: vi.fn(),
  selection: {
    runId: "r",
    view: "task",
    at: null,
    stepId: null,
    panel: null,
    artifactId: null,
    version: null,
    citationId: null,
  } as WorkbenchSelection,
  role: "user",
  context: false,
  runState: "ready",
  sessionStatus: "waiting",
  header: false,
  token: vi.fn(),
  download: vi.fn(),
  save: vi.fn(),
  command: vi.fn(),
  inbox: vi.fn(),
  scopeRevision: 1,
  approvalRun: "r",
  latestApprovalId: "a",
  recordedSubject: undefined as string | undefined,
  disconnect: vi.fn(),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push, replace: vi.fn() }),
  usePathname: () => "/sessions/s",
  useSearchParams: () => new URLSearchParams(mocks.search),
}));
vi.mock("@/providers/client-data-provider", () => ({
  useClientDataScope: () => ({ scope: mocks.scope, scopeRevision: mocks.scopeRevision }),
}));
vi.mock("@/lib/api/execution-view", () => ({
  executionViewApi: {
    getView: mocks.viewApi,
    getStep: mocks.stepApi,
    readContent: mocks.bodyApi,
    getTimeline: mocks.timelineApi,
    getEvents: mocks.eventsApi,
    streamEvents: mocks.streamApi,
  },
}));
vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
  useLocale: () => "en",
}));
vi.mock("@/hooks/use-mobile", () => ({ useIsMobile: () => ({ isMobile: false, isReady: true }) }));
vi.mock("@/providers/auth-provider", () => ({
  useAuth: () => ({ user: { id: "u", global_role: mocks.role }, loading: false }),
}));
vi.mock("@/providers/page-title-provider", () => ({ useReportPageTitle: () => {} }));
vi.mock("@/hooks/use-session-runs", () => ({
  useSessionRuns: () => ({
    items: [{ run_id: "r", status: "running" }],
    state: mocks.runState,
    refresh: vi.fn(),
  }),
}));
vi.mock("@/hooks/use-session-detail-view", () => ({
  useSessionDetailView: () => {
    const realWorkbench = useExecutionWorkbench();
    const [layout, setLayout] = useState<{ conversationOpen?: boolean }>({});
    return {
      session: {
        session_id: "s",
        status: mocks.sessionStatus,
        token_usage: mocks.header
          ? {
              total_tokens: 777,
              prompt_tokens: 700,
              completion_tokens: 77,
              call_count: 1,
              estimated_cost_usd: 0,
            }
          : null,
        mode: "ask",
        resource_bindings: mocks.context
          ? [{ resource_kind: "knowledge_base", resource_id: "kb" }]
          : [],
      },
      files: mocks.header
        ? [
            {
              id: "future-file",
              filename: "future.txt",
              filepath: "future",
              extension: "txt",
              size: 99,
            },
          ]
        : [],
      events: mocks.header
        ? [
            {
              type: "error",
              data: { error: "Future failure", event_id: "error", persist: true, created_at: 9 },
            },
          ]
        : [],
      timeline: [],
      sessionArtifacts: [],
      latestApproval: {
        approval_id: mocks.latestApprovalId,
        run_id: mocks.approvalRun,
        action: "test",
        risk_level: "low",
        payload: { tool_name: "test", subject_activity_id: mocks.recordedSubject },
      },
      latestAsk: {
        subject_activity_id: mocks.recordedSubject,
        run_id: mocks.approvalRun,
        ask_id: "ask",
        question: "Choose region",
        choices: ["EU"],
      },
      handleAskSend: mocks.ask,
      handleApprovalSend: mocks.send,
      workbench: mocks.real
        ? realWorkbench
        : {
            selection: mocks.selection,
            layout,
            setLayout,
            setSelection: mocks.select,
            loadState: "ready",
            refresh: vi.fn(),
            returnToLive: vi.fn(),
            view: {
              at: "cut",
              revision: 1,
              run: {
                run_id: mocks.selection.runId,
                status: "waiting",
                public_summary: "Persistent goal",
                completeness: { state: "complete", missing_fields: [] },
              },
              steps: [],
              messages: [{ message_id: "m", public_summary: "At boundary" }],
              approvals: [],
              artifacts: [],
            },
          },
    };
  },
}));
vi.mock("@/components/workspace/session-context-panel", () => ({
  useSessionContextRefs: () => ({}),
  SessionContextPanel: () => <div>Knowledge context</div>,
}));
vi.mock("@/components/session/virtualized-timeline", () => ({
  VirtualizedTimeline: () => <div>Live chat</div>,
}));
vi.mock("@/components/session/vnc-overlay", () => ({
  VNCOverlay: () => {
    useEffect(
      () => () => {
        mocks.disconnect();
      },
      [],
    );
    return <div data-vnc />;
  },
}));
vi.mock("@/components/session/operator-scope-dialog", () => ({ OperatorScopeDialog: () => null }));
vi.mock("@/components/session-model-picker", () => ({ SessionModelPicker: () => null }));
vi.mock("@/components/session-skill-picker", () => ({ SessionSkillPicker: () => null }));
vi.mock("@/components/session/thinking-toggle", () => ({ ThinkingToggle: () => null }));
vi.mock("@/components/markdown-content", () => ({
  MarkdownContent: ({ content }: { content: string }) => <p>{content}</p>,
}));
vi.mock("@/lib/api", () => ({
  fileApi: { downloadFile: mocks.download },
  sessionApi: { getTokenUsage: mocks.token },
}));
vi.mock("@/lib/download-blob", () => ({ downloadBlob: mocks.save }));
afterEach(() => {
  mocks.real = false;
  mocks.runState = "ready";
  mocks.sessionStatus = "waiting";
  mocks.header = false;
  mocks.role = "user";
  mocks.context = false;
  mocks.scopeRevision = 1;
  mocks.approvalRun = "r";
  mocks.latestApprovalId = "a";
  mocks.recordedSubject = undefined;
  vi.clearAllMocks();
});
vi.mock("@/lib/api/approvals", () => ({ approvalsApi: { list: mocks.inbox } }));
vi.mock("@/lib/api/session", () => ({ sessionApi: { decideApproval: mocks.command } }));
mocks.inbox.mockImplementation(async () => ({
  items: ["a", "ask"].map((approval_id) => ({
    approval_id,
    run_id: "r",
    source_entity_type: "session",
    source_entity_id: "s",
    status: "pending",
  })),
  limit: 200,
  offset: 0,
}));
mocks.command.mockResolvedValue({ run_id: "r", approval_id: "ask", decision: "approved" });
import { SessionDetailView } from "@/components/session/session-detail-view";
test("actual session composition exposes workbench and independent existing approval/ask commands", async () => {
  mocks.selection = parseSelection("run=r").selection;
  const { container, root, unmount } = await renderComponent(<SessionDetailView sessionId="s" />);
  expect(container.textContent).toContain("Persistent goal");
  const approve = [...container.querySelectorAll("button")].find(
    (b) => b.textContent === "approve",
  );
  const choose = [...container.querySelectorAll("button")].find((b) => b.textContent === "EU");
  expect(approve).toBeDefined();
  expect(choose).toBeDefined();
  await act(async () => choose!.click());
  expect(mocks.command).toHaveBeenCalledWith("ask", "approved", "EU", expect.anything());
  mocks.selection = parseSelection("run=r&at=cut").selection;
  await act(async () => root.render(<SessionDetailView sessionId="s" />));
  expect(container.textContent).toContain("At boundary");
  expect(container.querySelector("textarea")?.disabled).toBe(true);
  await unmount();
});

test("desktop context is reachable and auditor and old-run commands remain read only", async () => {
  mocks.context = true;
  mocks.role = "auditor";
  mocks.selection = parseSelection("run=r").selection;
  const result = await renderComponent(<SessionDetailView sessionId="s" />);
  const context = [...result.container.querySelectorAll("button")].find(
    (button) => button.textContent === "contextPanel",
  );
  expect(context).toBeDefined();
  await act(async () => context!.click());
  expect(document.body.textContent).toContain("Knowledge context");
  expect(result.container.querySelector("textarea")?.disabled).toBe(true);
  const approve = [...result.container.querySelectorAll("button")].find(
    (button) => button.textContent === "approve",
  );
  expect(approve?.disabled).toBe(true);
  await result.unmount();
  mocks.context = false;
  mocks.role = "user";
  mocks.selection = parseSelection("run=old").selection;
  const old = await renderComponent(<SessionDetailView sessionId="s" />);
  expect(old.container.querySelector("textarea")?.disabled).toBe(true);
  expect(old.container.querySelector("[hidden]")?.textContent).toContain("Live chat");
  await old.unmount();
});

test.each(["loading", "updating", "forbidden"])(
  "no-selection %s source authority cannot enable current commands",
  async (state) => {
    mocks.runState = state;
    mocks.sessionStatus = "pending";
    mocks.selection = parseSelection("").selection;
    const result = await renderComponent(<SessionDetailView sessionId="s" />);
    expect(result.container.querySelector("textarea")?.disabled).toBe(true);
    await result.unmount();
  },
);
test("confirmed empty pending session can send its first turn", async () => {
  mocks.runState = "empty";
  mocks.sessionStatus = "pending";
  mocks.selection = parseSelection("").selection;
  const result = await renderComponent(<SessionDetailView sessionId="s" />);
  expect(result.container.querySelector("textarea")?.disabled).toBe(false);
  await result.unmount();
});
test("actual live token dialog disappears at a historical cut and ignores a late response", async () => {
  mocks.header = true;
  mocks.selection = parseSelection("run=r").selection;
  let resolve!: (data: unknown) => void;
  mocks.token.mockReturnValue(
    new Promise((r) => {
      resolve = r;
    }),
  );
  const result = await renderComponent(<SessionDetailView sessionId="s" />);
  await act(async () =>
    result.container.querySelector<HTMLButtonElement>('[title="tokenUsageTitle"]')!.click(),
  );
  expect(document.querySelector('[role="dialog"]')).not.toBeNull();
  mocks.selection = parseSelection("run=r&at=cut").selection;
  await act(async () => result.root.render(<SessionDetailView sessionId="s" />));
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(result.container.textContent).not.toContain("777");
  expect(result.container.querySelector('[title="title"]')).toBeNull();
  await act(async () => resolve({ records: [{ id: "future", agent: "Future token record" }] }));
  expect(document.body.textContent).not.toContain("Future token record");
  await result.unmount();
});
test("actual file dialog and a pending download cannot cross into an old Run", async () => {
  mocks.header = true;
  mocks.selection = parseSelection("run=r").selection;
  let resolve!: (data: Blob) => void;
  mocks.download.mockReturnValue(
    new Promise((r) => {
      resolve = r;
    }),
  );
  const result = await renderComponent(<SessionDetailView sessionId="s" />);
  await act(async () =>
    result.container
      .querySelector<HTMLButtonElement>('button[aria-haspopup="dialog"]:not([title])')!
      .click(),
  );
  expect(document.body.textContent).toContain("future.txt");
  await act(async () =>
    document.querySelector<HTMLButtonElement>('[aria-label="downloadFile"]')!.click(),
  );
  mocks.selection = parseSelection("run=old").selection;
  await act(async () => result.root.render(<SessionDetailView sessionId="s" />));
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(document.body.textContent).not.toContain("future.txt");
  await act(async () => resolve(new Blob(["future body"])));
  expect(mocks.save).not.toHaveBeenCalled();
  await result.unmount();
});

test("mobile full-height conversation keeps both approval and clarification reachable in a scrollable composer", async () => {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 390, writable: true });
  mocks.selection = parseSelection("run=r").selection;
  const result = await renderComponent(<SessionDetailView sessionId="s" />);
  const section = result.container.querySelector<HTMLElement>("[data-conversation]")!;
  expect(section.hidden).toBe(true);
  await act(async () =>
    result.container.querySelector<HTMLButtonElement>("[data-conversation-bar] button")!.click(),
  );
  expect(section.dataset.mode).toBe("full-height");
  expect(section.hidden).toBe(false);
  const approve = [...section.querySelectorAll("button")].find(
    (button) => button.textContent === "approve",
  )!;
  const choose = [...section.querySelectorAll("button")].find(
    (button) => button.textContent === "EU",
  )!;
  expect(approve.disabled).toBe(false);
  expect(choose.disabled).toBe(false);
  expect(approve.closest(".overflow-y-auto")).toBe(choose.closest(".overflow-y-auto"));
  expect(approve.closest(".overflow-y-auto")?.className).toContain("max-h-[70%]");
  await act(async () => choose.click());
  expect(mocks.command).toHaveBeenCalledWith("ask", "approved", "EU", expect.anything());
  await result.unmount();
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 1024, writable: true });
});
test("the actual live error sheet is removed when entering an older boundary", async () => {
  mocks.header = true;
  mocks.selection = parseSelection("run=r").selection;
  const result = await renderComponent(<SessionDetailView sessionId="s" />);
  await act(async () =>
    result.container.querySelector<HTMLButtonElement>('[title="title"]')!.click(),
  );
  expect(document.body.textContent).toContain("Future failure");
  mocks.selection = parseSelection("run=r&at=cut").selection;
  await act(async () => result.root.render(<SessionDetailView sessionId="s" />));
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(document.body.textContent).not.toContain("Future failure");
  await result.unmount();
});
test("session Debug mounts shared trace while retaining conversation composer", async () => {
  mocks.selection = parseSelection("run=r").selection;
  const result = await renderComponent(<SessionDetailView sessionId="s" />);
  const composer = result.container.querySelector("textarea");
  expect(composer).not.toBeNull();
  mocks.selection = { ...mocks.selection, view: "debug" };
  await act(async () => result.root.render(<SessionDetailView sessionId="s" />));
  expect(result.container.querySelector("textarea")).toBe(composer);
  expect(result.container.querySelector('section[aria-label="title"]')).not.toBeNull();
  expect(result.container.querySelector('[aria-label="kind"]')).not.toBeNull();
  await result.unmount();
});

test("actual session playback and shared coordinator mask immediately and show only historical summaries", async () => {
  mocks.real = true;
  mocks.search = "run=r";
  const completeness = { state: "complete", missing_fields: [], missing_intervals: [] };
  const live = {
    at: "live-cut",
    revision: 2,
    hidden_count: 0,
    next_cursor: null,
    steps: [],
    run: {
      run_id: "r",
      projection_revision: 2,
      status: "running",
      public_summary: "Live secret",
      admitted_at: "2026-01-01T00:00:00Z",
      as_of: "2026-01-01T00:01:00Z",
      latest_available: "2026-01-01T00:01:00Z",
      completeness,
    },
    messages: [{ message_id: "future", public_summary: "Future message" }],
    approvals: [],
    artifacts: [],
  };
  mocks.viewApi.mockResolvedValue(live);
  mocks.eventsApi.mockResolvedValue({ events: [], next_cursor: null });
  mocks.streamApi.mockImplementation(() => new Promise(() => {}));
  let resolve!: (x: unknown) => void;
  mocks.timelineApi.mockImplementation(() => new Promise((r) => (resolve = r)));
  const { container, root, unmount } = await renderComponent(<SessionDetailView sessionId="s" />);
  expect(container.textContent).toContain("Live secret");
  await act(async () =>
    Array.from(container.querySelectorAll("button"))
      .find((b) => b.textContent === "previousEvent")!
      .click(),
  );
  expect(container.textContent).not.toContain("Live secret");
  expect(container.textContent).not.toContain("Future message");
  expect(container.querySelector("textarea")?.disabled).toBe(true);
  await act(async () => resolve({ run_id: "r", at: "history-cut" }));
  expect(mocks.push).toHaveBeenLastCalledWith(expect.stringContaining("at=history-cut"), {
    scroll: false,
  });
  mocks.search = "run=r&at=history-cut";
  mocks.viewApi.mockResolvedValue({
    ...live,
    at: "history-cut",
    revision: 1,
    run: {
      ...live.run,
      projection_revision: 1,
      public_summary: "Historical task",
      as_of: "2026-01-01T00:00:10Z",
    },
    messages: [{ message_id: "past", public_summary: "Public summary at cut" }],
  });
  await act(async () => root.render(<SessionDetailView sessionId="s" />));
  expect(container.textContent).toContain("Historical task");
  expect(container.textContent).toContain("Public summary at cut");
  expect(container.textContent).not.toContain("Future message");
  expect(container.querySelector("textarea")?.disabled).toBe(true);
  await act(async () =>
    Array.from(container.querySelectorAll("button"))
      .find((b) => b.textContent === "returnLive")!
      .click(),
  );
  expect(mocks.push.mock.calls.at(-1)![0]).not.toContain("at=");
  mocks.search = "run=r";
  mocks.viewApi.mockResolvedValue({
    ...live,
    revision: 3,
    run: { ...live.run, projection_revision: 3, public_summary: "Fresh live" },
  });
  await act(async () => root.render(<SessionDetailView sessionId="s" />));
  expect(container.textContent).toContain("Fresh live");
  expect(container.textContent).not.toContain("Public summary at cut");
  await unmount();
});

test("an old Run event cannot populate current actionable approval or clarification", async () => {
  mocks.approvalRun = "old";
  mocks.selection = parseSelection("run=r").selection;
  const r = await renderComponent(<SessionDetailView sessionId="s" />);
  expect(
    Array.from(r.container.querySelectorAll("button")).find((b) => b.textContent === "approve"),
  ).toBeUndefined();
  expect(
    Array.from(r.container.querySelectorAll("button")).find((b) => b.textContent === "EU"),
  ).toBeUndefined();
  await r.unmount();
});

test.each(["scope", "oldRun", "loading"])(
  "VNC %s loss closes takeover and requires a new gesture on return",
  async (change) => {
    mocks.sessionStatus = "running";
    mocks.selection = parseSelection("run=r").selection;
    const r = await renderComponent(<SessionDetailView sessionId="s" />);
    const takeover = Array.from(r.container.querySelectorAll("button")).find(
      (b) => b.textContent === "takeover",
    );
    expect(takeover).toBeDefined();
    await act(async () => takeover!.click());
    expect(r.container.querySelector("[data-vnc]")).not.toBeNull();
    if (change === "scope") mocks.scopeRevision++;
    if (change === "oldRun") mocks.selection = parseSelection("run=old").selection;
    if (change === "loading") mocks.runState = "updating";
    await act(async () => r.root.render(<SessionDetailView sessionId="s" />));
    expect(r.container.querySelector("[data-vnc]")).toBeNull();
    expect(mocks.disconnect).toHaveBeenCalledTimes(1);
    mocks.selection = parseSelection("run=r").selection;
    mocks.runState = "ready";
    await act(async () => r.root.render(<SessionDetailView sessionId="s" />));
    expect(r.container.querySelector("[data-vnc]")).toBeNull();
    await r.unmount();
  },
);

test("actual session entry loads exact-cut detail body and a reader denial masks the entire workbench", async () => {
  mocks.real = true;
  mocks.search = "run=r&step=step&panel=input-output";
  const completeness = { state: "complete", missing_fields: [], missing_intervals: [] };
  const step = {
    step_id: "step",
    run_id: "r",
    at: "live-cut",
    projection_revision: 1,
    kind: "tool",
    status: "completed",
    attempt_id: "attempt",
    public_summary: "Selected invocation",
    input_ref: { content_id: "i", availability: "available" },
    output_ref: { content_id: "o", availability: "available" },
    completeness,
  };
  mocks.viewApi.mockResolvedValue({
    at: "live-cut",
    revision: 1,
    run: {
      run_id: "r",
      projection_revision: 1,
      status: "running",
      public_summary: "Current task",
      completeness,
    },
    steps: [step],
    approvals: [],
    artifacts: [],
    next_cursor: null,
  });
  mocks.stepApi.mockResolvedValue(step);
  mocks.eventsApi.mockResolvedValue({ events: [], next_cursor: null });
  mocks.streamApi.mockImplementation(() => new Promise(() => {}));
  mocks.bodyApi
    .mockResolvedValueOnce({
      availability: "available",
      at: "live-cut",
      content: "Authorized body",
      content_type: "text/plain",
      redacted: false,
      truncated: false,
    })
    .mockRejectedValueOnce(new ApiError(403, "denied"));
  const r = await renderComponent(<SessionDetailView sessionId="s" />);
  await act(async () =>
    Array.from(document.querySelectorAll("button"))
      .find((b) => b.textContent === "loadOutput")!
      .click(),
  );
  expect(document.body.textContent).toContain("Authorized body");
  expect(mocks.bodyApi.mock.calls[0][2]).toMatchObject({
    at: "live-cut",
    content_kind: "output",
    limit_bytes: 65536,
  });
  await act(async () =>
    Array.from(document.querySelectorAll("button"))
      .find((b) => b.textContent === "loadInput")!
      .click(),
  );
  expect(document.body.textContent).not.toContain("Authorized body");
  expect(document.body.textContent).not.toContain("Current task");
  expect(document.body.textContent).not.toContain("Selected invocation");
  await r.unmount();
});

test("detail Return live selects the confirmed current Run from an explicit old Run", async () => {
  mocks.selection = parseSelection("run=old&panel=overview").selection;
  const r = await renderComponent(<SessionDetailView sessionId="s" />);
  await act(async () =>
    Array.from(document.querySelectorAll("button"))
      .find((b) => b.textContent === "returnLive")!
      .click(),
  );
  expect(mocks.select).toHaveBeenCalledWith(
    expect.objectContaining({ runId: "r", at: null, stepId: null }),
  );
  await r.unmount();
});

test("actual step A approval detail cannot decide conversation's latest approval B", async () => {
  mocks.real = true;
  mocks.latestApprovalId = "b";
  mocks.search = "run=r&step=step-a&panel=approval";
  const completeness = { state: "complete", missing_fields: [], missing_intervals: [] };
  const step = {
    step_id: "step-a",
    activity_id: "activity-a",
    run_id: "r",
    at: "cut-a",
    projection_revision: 1,
    kind: "tool",
    status: "waiting",
    attempt_id: "attempt",
    public_summary: "Step A",
    completeness,
  };
  mocks.viewApi.mockResolvedValue({
    at: "cut-a",
    revision: 1,
    run: { run_id: "r", projection_revision: 1, status: "waiting", completeness },
    steps: [step],
    approvals: [
      {
        approval_id: "a",
        subject_activity_id: "activity-a",
        approval_kind: "tool_effect",
        status: "pending",
      },
    ],
    artifacts: [],
    next_cursor: null,
  });
  mocks.stepApi.mockResolvedValue(step);
  mocks.eventsApi.mockResolvedValue({ events: [], next_cursor: null });
  mocks.streamApi.mockImplementation(() => new Promise(() => {}));
  const original = mocks.inbox.getMockImplementation();
  mocks.inbox.mockResolvedValue({
    items: [
      {
        approval_id: "b",
        run_id: "r",
        source_entity_type: "session",
        source_entity_id: "s",
        status: "pending",
        subject_activity_id: "activity-b",
      },
      {
        approval_id: "a",
        run_id: "r",
        source_entity_type: "session",
        source_entity_id: "s",
        status: "pending",
        subject_activity_id: "activity-a",
        subject_label: "Target A",
        risk_summary: "Risk A",
      },
    ],
    limit: 200,
    offset: 0,
  });
  const r = await renderComponent(<SessionDetailView sessionId="s" />);
  const panel = document.querySelector("[data-execution-detail]")!;
  expect(panel.textContent).toContain("Target A");
  await act(async () =>
    Array.from(panel.querySelectorAll("button"))
      .find((b) => b.textContent === "approve")!
      .click(),
  );
  expect(mocks.command).toHaveBeenCalledWith("a", "approved", "", expect.anything());
  expect(mocks.command.mock.calls.every((call) => call[0] === "a")).toBe(true);
  await r.unmount();
  if (original) mocks.inbox.mockImplementation(original);
});

test.each(["approve", "EU"])(
  "conversation %s carries its recorded subject into click-time preflight",
  async (label) => {
    mocks.recordedSubject = "subject-a";
    mocks.selection = parseSelection("run=r").selection;
    const original = mocks.inbox.getMockImplementation();
    mocks.inbox.mockResolvedValue({
      items: ["a", "ask"].map((approval_id) => ({
        approval_id,
        run_id: "r",
        source_entity_type: "session",
        source_entity_id: "s",
        subject_activity_id: "subject-b",
        status: "pending",
      })),
      limit: 200,
      offset: 0,
    });
    const r = await renderComponent(<SessionDetailView sessionId="s" />);
    await act(async () =>
      Array.from(r.container.querySelectorAll("button"))
        .find((b) => b.textContent === label)!
        .click(),
    );
    expect(mocks.command).not.toHaveBeenCalled();
    expect(r.container.textContent).toContain("decision.unavailable");
    await r.unmount();
    if (original) mocks.inbox.mockImplementation(original);
  },
);
