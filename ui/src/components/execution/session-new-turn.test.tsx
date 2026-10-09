// @vitest-environment jsdom
import { act } from "react";
import { expect, test, vi } from "vitest";

import type { SSEEventData } from "@/lib/api/types";
import { parseSelection } from "@/lib/execution-view/url-state";

import { renderComponent } from "@/test-utils/render";
const mocks = vi.hoisted(() => ({
  chat: vi.fn(),
  list: vi.fn(),
  replace: vi.fn(),
  search: "run=R1",
  t: (key: string) => key,
}));
vi.mock("next-intl", () => ({ useTranslations: () => mocks.t, useLocale: () => "en" }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace, push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(mocks.search),
  usePathname: () => "/sessions/s",
}));
vi.mock("@/providers/auth-provider", () => ({
  useAuth: () => ({ user: { id: "u", global_role: "user" }, loading: false }),
}));
vi.mock("@/providers/client-data-provider", () => ({
  useClientDataScope: () => ({ scope: { userId: "u", workspaceId: "w" }, scopeRevision: 1 }),
}));
vi.mock("@/hooks/use-require-auth", () => ({
  useRequireAuth: () => ({ requireAuth: () => true }),
}));
vi.mock("@/providers/page-title-provider", () => ({ useReportPageTitle: () => {} }));
vi.mock("@/hooks/use-mobile", () => ({ useIsMobile: () => ({ isMobile: false, isReady: true }) }));
vi.mock("@/hooks/use-execution-workbench", () => ({
  useExecutionWorkbench: () => ({
    selection: parseSelection(mocks.search).selection,
    setSelection: vi.fn(),
    layout: {},
    setLayout: vi.fn(),
    loadState: "ready",
    view: {
      run: {
        run_id: "R1",
        status: "completed",
        public_summary: "R1 facts",
        completeness: { state: "complete" },
      },
      steps: [],
      messages: [],
      approvals: [],
      artifacts: [],
    },
  }),
}));
vi.mock("@/lib/api/execution-view", () => ({ executionViewApi: { listRuns: mocks.list } }));
vi.mock("@/lib/api/artifacts", () => ({
  artifactsApi: { listBySession: async () => ({ artifacts: [] }) },
}));
vi.mock("@/lib/api/session", () => ({
  sessionApi: {
    getSessionDetail: async () => ({
      session_id: "s",
      status: "completed",
      mode: "ask",
      files: [],
    }),
    getSessionEvents: async () => ({
      events: [
        {
          event_type: "message",
          run_id: "R1",
          payload: {
            event_id: "R1-created",
            role: "user",
            message: "first",
            persist: true,
            created_at: 1,
          },
        },
      ],
      has_earlier: false,
    }),
    getSessionFiles: async () => [],
    chat: mocks.chat,
  },
}));
vi.mock("@/components/session/virtualized-timeline", () => ({
  VirtualizedTimeline: () => <div>Live conversation</div>,
}));
vi.mock("@/components/session/operator-scope-dialog", () => ({ OperatorScopeDialog: () => null }));
vi.mock("@/components/session-model-picker", () => ({ SessionModelPicker: () => null }));
vi.mock("@/components/session-skill-picker", () => ({ SessionSkillPicker: () => null }));
vi.mock("@/components/session/thinking-toggle", () => ({ ThinkingToggle: () => null }));
vi.mock("@/components/markdown-content", () => ({
  MarkdownContent: ({ content }: { content: string }) => <p>{content}</p>,
}));
vi.mock("@/components/workspace/session-context-panel", () => ({
  useSessionContextRefs: () => ({}),
  SessionContextPanel: () => null,
}));
import { SessionDetailView } from "@/components/session/session-detail-view";

test("actual ChatInput → handleSend → sendMessage admission updates source membership and keeps explicit old selection read only", async () => {
  vi.useFakeTimers();
  mocks.chat.mockReturnValue(() => {});
  mocks.list.mockResolvedValue({
    items: [{ run_id: "R1", status: "completed" }],
    next_cursor: "old-tail",
    completeness: { state: "complete" },
  });
  const result = await renderComponent(<SessionDetailView sessionId="s" />);
  const input = result.container.querySelector("textarea")!;
  expect(input.disabled).toBe(false);
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(
      input,
      "new turn",
    );
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () =>
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })),
  );
  const send = mocks.chat.mock.calls.find((call) => call[1].request_id);
  expect(send?.[1].message).toBe("new turn");
  expect(input.disabled).toBe(true);
  const event: SSEEventData = {
    type: "message",
    data: {
      role: "user",
      message: "new turn",
      run_id: "R2",
      event_id: "R2-created",
      persist: true,
      created_at: 2,
      schema_version: 1,
      visibility: "user",
      channel: "ui",
    },
  };
  await act(async () => send![2](event));
  expect(input.disabled).toBe(true);
  expect(result.container.textContent).toContain("listState.updating");
  mocks.list.mockResolvedValueOnce({
    items: [{ run_id: "R0", status: "completed" }],
    next_cursor: null,
    completeness: { state: "complete" },
  });
  await act(async () =>
    [...result.container.querySelectorAll("button")]
      .find((button) => button.textContent === "moreRuns")!
      .click(),
  );
  expect(mocks.list.mock.calls.at(-1)?.[0].cursor).toBe("old-tail");
  expect(result.container.textContent).toContain("listState.updating");
  expect(input.disabled).toBe(true);
  mocks.list.mockResolvedValue({
    items: [
      { run_id: "R2", status: "running" },
      { run_id: "R1", status: "completed" },
    ],
    next_cursor: null,
    completeness: { state: "complete" },
  });
  await act(async () => vi.advanceTimersByTime(1000));
  expect(
    result.container.querySelector("select")?.querySelector('option[value="R2"]'),
  ).not.toBeNull();
  expect(input.disabled).toBe(true);
  expect(mocks.replace).not.toHaveBeenCalled();
  expect(result.container.querySelector('[title="tokenUsageTitle"]')).toBeNull();
  await result.unmount();
  vi.useRealTimers();
});
