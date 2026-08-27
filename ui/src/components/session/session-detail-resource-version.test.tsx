// @vitest-environment jsdom

import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { mockNavigation, mockNextIntl } from "@/test-utils/mocks";
import { renderComponent } from "@/test-utils/render";

const api = vi.hoisted(() => {
  const state = { upgraded: false };
  const v1 = {
    binding_id: "b1",
    resource_kind: "knowledge_base" as const,
    resource_id: "kb1",
    version_id: "v1",
    is_current: true,
  };
  const v2 = {
    ...v1,
    binding_id: "b2",
    version_id: "v2",
    supersedes_binding_id: "b1",
  };
  return {
    state,
    v1,
    v2,
    getSessionDetail: vi.fn(async () => ({
      session_id: "s1",
      title: "Versioned session",
      latest_message: "",
      latest_message_at: "2026-07-29T00:00:00Z",
      status: "completed" as const,
      unread_message_count: 0,
      mode: "agent" as const,
      thinking_enabled: false,
      resource_bindings: [v1],
    })),
    getSessionEvents: vi.fn(async () => ({
      events: [
        {
          event: "message",
          data: {
            event_id: "1",
            role: "assistant",
            message: "历史回答",
            resource_bindings: [v1],
          },
        },
      ],
      next_cursor: null,
      prev_cursor: null,
      has_earlier: false,
    })),
    getSessionFiles: vi.fn(async () => []),
    getResourceBindings: vi.fn(async () => (state.upgraded ? [v2] : [v1])),
    getAvailableResourceVersions: vi.fn(async () => [v1, v2]),
    upgradeResourceBinding: vi.fn(async () => {
      state.upgraded = true;
      return {
        old_binding_id: "b1",
        new_binding_id: "b2",
        current_version_id: "v2",
      };
    }),
    clearUnreadMessageCount: vi.fn(async () => undefined),
    updateSessionConfig: vi.fn(),
    stopSession: vi.fn(),
  };
});

const stream = vi.hoisted(() => ({
  sendMessage: vi.fn(async () => undefined),
  resetStreams: vi.fn(),
  markSessionMissing: vi.fn(),
  enableDebugStream: vi.fn(),
}));

vi.mock("@/lib/api/session", () => ({
  sessionApi: {
    getSessionDetail: api.getSessionDetail,
    getSessionEvents: api.getSessionEvents,
    getSessionFiles: api.getSessionFiles,
    getResourceBindings: api.getResourceBindings,
    getAvailableResourceVersions: api.getAvailableResourceVersions,
    upgradeResourceBinding: api.upgradeResourceBinding,
    clearUnreadMessageCount: api.clearUnreadMessageCount,
    updateSessionConfig: api.updateSessionConfig,
    stopSession: api.stopSession,
  },
}));

vi.mock("@/hooks/use-session-streams", () => ({
  useSessionStreams: () => ({
    streaming: false,
    streamStatus: "idle",
    streamError: null,
    sendMessage: stream.sendMessage,
    resetStreams: stream.resetStreams,
    markSessionMissing: stream.markSessionMissing,
    enableDebugStream: stream.enableDebugStream,
  }),
}));

vi.mock("@tanstack/react-virtual", () => ({
  useVirtualizer: ({ count }: { count: number }) => ({
    getTotalSize: () => count * 120,
    getVirtualItems: () =>
      Array.from({ length: count }, (_, index) => ({
        index,
        start: index * 120,
      })),
    measureElement: () => undefined,
    scrollToIndex: () => undefined,
  }),
}));

vi.mock("next-intl", () =>
  mockNextIntl(
    {
      resourceVersionsAria: "资源版本上下文",
      upgradeContext: "升级上下文",
      confirmUpgrade: (values) => `确认升级到 ${values?.version ?? ""}`,
    },
    "zh-CN",
  ),
);

vi.mock("next/navigation", () => mockNavigation());

vi.mock("@/hooks/use-mobile", () => ({
  useIsMobile: () => ({ isMobile: false, isReady: true }),
}));

vi.mock("@/hooks/use-require-auth", () => ({
  useRequireAuth: () => ({ requireAuth: () => true }),
}));

vi.mock("@/components/markdown-content", () => ({
  MarkdownContent: ({ content }: { content: string }) => <p>{content}</p>,
}));

vi.mock("@/components/workspace/knowledge-context-panel", () => ({
  KnowledgeContextPanel: () => <div>knowledge context</div>,
}));

vi.mock("@/components/workspace/codebase-context-panel", () => ({
  CodebaseContextPanel: () => <div>codebase context</div>,
}));

vi.mock("@/components/session/chat-input", () => ({
  ChatInput: () => <div>chat input</div>,
}));

vi.mock("@/components/session/session-header", () => ({
  SessionHeader: () => <div>session header</div>,
}));

vi.mock("@/components/session/operator-scope-dialog", () => ({
  OperatorScopeDialog: () => null,
}));

vi.mock("@/components/session/file-preview-panel", () => ({
  FilePreviewPanel: () => null,
}));

vi.mock("@/components/session/tool-preview-panel", () => ({
  ToolPreviewPanel: () => null,
}));

vi.mock("@/components/session/approval-actions-bar", () => ({
  ApprovalActionsBar: () => null,
}));

vi.mock("@/components/session/session-mode-toggle", () => ({
  SessionModeToggle: () => null,
}));

vi.mock("@/components/session-model-picker", () => ({
  SessionModelPicker: () => null,
}));

vi.mock("@/components/session-skill-picker", () => ({
  SessionSkillPicker: () => null,
}));

vi.mock("@/components/session/thinking-toggle", () => ({
  ThinkingToggle: () => null,
}));

vi.mock("@/components/session/vnc-overlay", () => ({
  VNCOverlay: () => null,
}));

import { SessionDetailView } from "@/components/session/session-detail-view";

async function settle() {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

describe("SessionDetailView resource version integration", () => {
  beforeEach(() => {
    api.state.upgraded = false;
    vi.clearAllMocks();
  });

  afterEach(() => {
    document.body.replaceChildren();
  });

  it("confirms v2 for the current pin while the rendered historical event remains v1", async () => {
    const { container, unmount } = await renderComponent(<SessionDetailView sessionId="s1" />);
    await settle();
    await settle();

    const current = container.querySelector("section[aria-label='资源版本上下文']");
    const historical = container.querySelector("span[aria-label='资源版本上下文']");
    expect(current?.textContent).toContain("knowledge_base: v1");
    expect(historical?.textContent).toBe("v1");
    expect(container.textContent).toContain("历史回答");

    const upgrade = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent === "升级上下文",
    );
    expect(upgrade).toBeTruthy();
    await act(async () => {
      upgrade?.click();
    });

    const confirm = Array.from(document.querySelectorAll("button")).find(
      (button) => button.textContent === "确认升级到 v2",
    );
    expect(confirm).toBeTruthy();
    await act(async () => {
      confirm?.click();
    });
    await settle();

    expect(api.upgradeResourceBinding).toHaveBeenCalledTimes(1);
    expect(api.upgradeResourceBinding).toHaveBeenCalledWith("s1", "knowledge_base", "v2");
    expect(current?.textContent).toContain("knowledge_base: v2");
    expect(historical?.textContent).toBe("v1");

    await unmount();
  });
});
