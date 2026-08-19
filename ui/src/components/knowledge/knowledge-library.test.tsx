// @vitest-environment jsdom

import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockAuth, mockNavigation, mockNextIntl, mockSonner } from "@/test-utils/mocks";
import { renderComponent } from "@/test-utils/render";

const mocks = vi.hoisted(() => ({
  createSession: vi.fn(),
  createBuild: vi.fn(),
  legacyReindex: vi.fn(),
  listKnowledgeBases: vi.fn(),
  listVersions: vi.fn(),
  push: vi.fn(),
}));

vi.mock("next/navigation", () => mockNavigation({ push: mocks.push }));
vi.mock("next-intl", () =>
  mockNextIntl({
    startAsk: "Ask",
    startAgent: "Agent",
    reindex: "Re-index",
    viewBuild: "View build",
  }),
);
vi.mock("sonner", () => mockSonner());
vi.mock("@/providers/auth-provider", () => mockAuth());
vi.mock("@/lib/api/knowledge", () => ({
  knowledgeApi: {
    list: mocks.listKnowledgeBases,
    listVersions: mocks.listVersions,
    createBuild: mocks.createBuild,
    retryBuild: vi.fn(),
    cancelBuild: vi.fn(),
    ingestStream: vi.fn(() => vi.fn()),
    listDocuments: vi.fn(),
    delete: vi.fn(),
    deleteDocument: vi.fn(),
    reindex: mocks.legacyReindex,
  },
}));
vi.mock("@/lib/api/session", () => ({ sessionApi: { createSession: mocks.createSession } }));
vi.mock("@/components/knowledge/add-document-dialog", () => ({ AddDocumentDialog: () => null }));
vi.mock("@/components/knowledge/create-kb-dialog", () => ({ CreateKBDialog: () => null }));
vi.mock("@/components/confirm-delete-dialog", () => ({ ConfirmDeleteDialog: () => null }));
vi.mock("@/components/empty-state", () => ({ EmptyState: () => null }));
vi.mock("@/components/page-header", () => ({
  PageHeader: ({ title, actions }: { title: React.ReactNode; actions: React.ReactNode }) => (
    <>
      {title}
      {actions}
    </>
  ),
}));
vi.mock("@/components/ui/card", () => ({
  Card: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  CardContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  CardDescription: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  CardHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  CardTitle: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/ui/scroll-area", () => ({
  ScrollArea: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
vi.mock("@/components/ui/dropdown-menu", () => ({
  DropdownMenu: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuTrigger: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  DropdownMenuContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DropdownMenuItem: ({
    children,
    onSelect,
    onClick,
    disabled,
  }: {
    children: React.ReactNode;
    onSelect?: () => void;
    onClick?: () => void;
    disabled?: boolean;
  }) => (
    <button onClick={onSelect ?? onClick} disabled={disabled}>
      {children}
    </button>
  ),
}));
vi.mock("@/lib/icons", () => ({
  IconAdd: () => null,
  IconDelete: () => null,
  IconKnowledge: () => null,
  IconLoading: () => null,
  IconMore: () => null,
  IconRefresh: () => null,
}));

import { KnowledgeLibrary } from "./knowledge-library";

function findButton(container: HTMLDivElement, label: string): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll("button")).find(
    (candidate) => candidate.textContent?.trim() === label,
  );
  expect(button).toBeDefined();
  return button as HTMLButtonElement;
}

async function renderLibrary() {
  return renderComponent(<KnowledgeLibrary />);
}

describe("knowledge task starts", () => {
  afterEach(() => {
    mocks.createSession.mockReset();
    mocks.createBuild.mockReset();
    mocks.legacyReindex.mockReset();
    mocks.listKnowledgeBases.mockReset();
    mocks.listVersions.mockReset();
    mocks.push.mockReset();
    document.body.replaceChildren();
  });

  it("starts sessions with the mode selected by the rendered Ask and Agent buttons", async () => {
    mocks.listKnowledgeBases.mockResolvedValue({
      knowledge_bases: [
        {
          id: "kb-1",
          name: "Handbook",
          status: "parsing",
          doc_count: 2,
          ready_doc_count: 1,
          active_version_id: "version-published",
          ingest_task_id: "candidate-build",
        },
      ],
    });
    mocks.listVersions.mockResolvedValue({
      knowledge_base_id: "kb-1",
      active_version_id: "version-published",
      versions: [],
    });
    mocks.createSession
      .mockResolvedValueOnce({ session_id: "session-ask" })
      .mockResolvedValueOnce({ session_id: "session-agent" });
    const { container, unmount } = await renderLibrary();

    await act(async () => {
      findButton(container, "Ask").click();
      await Promise.resolve();
    });
    await act(async () => {
      findButton(container, "Agent").click();
      await Promise.resolve();
    });

    expect(mocks.createSession).toHaveBeenNthCalledWith(1, {
      knowledge_base_id: "kb-1",
      knowledge_base_version_id: "version-published",
      mode: "ask",
    });
    expect(mocks.createSession).toHaveBeenNthCalledWith(2, {
      knowledge_base_id: "kb-1",
      knowledge_base_version_id: "version-published",
      mode: "agent",
    });
    await unmount();
  });

  it("uses the build command and disables duplicate creation while a build is active", async () => {
    mocks.listKnowledgeBases.mockResolvedValue({
      knowledge_bases: [
        {
          id: "kb-1",
          name: "Handbook",
          status: "ready",
          doc_count: 2,
          ready_doc_count: 2,
          active_version_id: "version-published",
        },
      ],
    });
    const idleHistory = {
      knowledge_base_id: "kb-1",
      active_version_id: "version-published",
      active_build: null,
      versions: [
        {
          id: "version-published",
          knowledge_base_id: "kb-1",
          state: "ready",
          capabilities: {},
          degraded_reasons: [],
          metrics: {},
          created_at: "2026-07-29T00:00:00Z",
          published_at: "2026-07-29T00:01:00Z",
          is_active: true,
          is_published: true,
          is_candidate: false,
        },
      ],
    };
    const activeHistory = {
      ...idleHistory,
      active_build: {
        id: "build-1",
        knowledge_base_id: "kb-1",
        version_id: "version-candidate",
        parent_version_id: "version-published",
        command_key: "command",
        state: "running",
        phase: "chunk",
        progress: 0.5,
        capabilities: [],
        degraded_reasons: [],
        metrics: {},
        last_event_seq: 1,
        created_at: "2026-07-30T00:00:00Z",
        can_retry: false,
        can_cancel: true,
      },
    };
    mocks.listVersions.mockResolvedValueOnce(idleHistory).mockResolvedValue(activeHistory);
    mocks.createBuild.mockResolvedValue({
      ...idleHistory.versions[0],
      id: "version-candidate",
      state: "building",
      is_active: false,
      is_published: false,
      is_candidate: true,
    });

    const { container, unmount } = await renderLibrary();
    await act(async () => {
      findButton(container, "Re-index").click();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mocks.createBuild).toHaveBeenCalledWith("kb-1");
    expect(mocks.legacyReindex).not.toHaveBeenCalled();
    expect(findButton(container, "View build").disabled).toBe(true);
    await unmount();
  });
});
