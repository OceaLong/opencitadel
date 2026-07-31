// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SessionDetailView } from "@/components/session-detail-view";

(
  globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
).IS_REACT_ACT_ENVIRONMENT = true;

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
      knowledge_base_id: "kb1",
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
    listCheckpoints: vi.fn(async () => ({ checkpoints: [] })),
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
    restoreCheckpoint: vi.fn(),
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
    listCheckpoints: api.listCheckpoints,
    getResourceBindings: api.getResourceBindings,
    getAvailableResourceVersions: api.getAvailableResourceVersions,
    upgradeResourceBinding: api.upgradeResourceBinding,
    clearUnreadMessageCount: api.clearUnreadMessageCount,
    updateSessionConfig: api.updateSessionConfig,
    stopSession: api.stopSession,
    restoreCheckpoint: api.restoreCheckpoint,
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

vi.mock("next-intl", () => ({
  useLocale: () => "zh-CN",
  useTranslations: () => (key: string, values?: Record<string, string | number>) => {
    const messages: Record<string, string> = {
      resourceVersionsAria: "资源版本上下文",
      upgradeContext: "升级上下文",
      confirmUpgrade: `确认升级到 ${values?.version ?? ""}`,
    };
    return messages[key] ?? (values ? `${key}:${JSON.stringify(values)}` : key);
  },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn() }),
}));

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

vi.mock("@/components/chat-input", () => ({
  ChatInput: () => <div>chat input</div>,
}));

vi.mock("@/components/session-header", () => ({
  SessionHeader: () => <div>session header</div>,
}));

vi.mock("@/components/checkpoint-restore-dialog", () => ({
  CheckpointRestoreDialog: () => null,
}));

vi.mock("@/components/operator-scope-dialog", () => ({
  OperatorScopeDialog: () => null,
}));

vi.mock("@/components/file-preview-panel", () => ({
  FilePreviewPanel: () => null,
}));

vi.mock("@/components/tool-preview-panel", () => ({
  ToolPreviewPanel: () => null,
}));

vi.mock("@/components/gate-actions-bar", () => ({
  GateActionsBar: () => null,
}));

vi.mock("@/components/plan-approval-bar", () => ({
  PlanApprovalBar: () => null,
}));

vi.mock("@/components/plan-panel", () => ({
  PlanPanel: () => null,
}));

vi.mock("@/components/session-mode-toggle", () => ({
  SessionModeToggle: () => null,
}));

vi.mock("@/components/session-model-picker", () => ({
  SessionModelPicker: () => null,
}));

vi.mock("@/components/session-skill-picker", () => ({
  SessionSkillPicker: () => null,
}));

vi.mock("@/components/thinking-toggle", () => ({
  ThinkingToggle: () => null,
}));

vi.mock("@/components/vnc-overlay", () => ({
  VNCOverlay: () => null,
}));

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
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(<SessionDetailView sessionId="s1" />);
    });
    await settle();
    await settle();

    const current = container.querySelector("section[aria-label='资源版本上下文']");
    const historical = container.querySelector("[aria-label='消息资源版本']");
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

    await act(async () => root.unmount());
  });
});
