// @vitest-environment jsdom

import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockAuth, mockNavigation, mockNextIntl, mockSonner } from "@/test-utils/mocks";
import { renderComponent } from "@/test-utils/render";

const mocks = vi.hoisted(() => ({
  createSession: vi.fn(),
  createBuild: vi.fn(),
  createSnapshot: vi.fn(),
  listCodebases: vi.fn(),
  listVersions: vi.fn(),
  push: vi.fn(),
}));

vi.mock("next/navigation", () => mockNavigation({ push: mocks.push }));
vi.mock("next-intl", () =>
  mockNextIntl({
    create: "New codebase",
    deleteDescription: "Delete codebase",
    deleteTitle: "Delete?",
    download: "Download",
    empty: "Empty",
    librarySubtitle: "Library",
    loadError: "Load failed",
    reanalyze: "Re-analyze",
    startAgent: "Agent",
    startAsk: "Ask",
    startTaskFailed: "Start failed",
    title: "Codebase",
    viewBuild: "View build",
    "status.ready": "Ready",
    statusFileCount: (values) => `${values?.status ?? ""} · ${values?.count ?? 0} files`,
  }),
);
vi.mock("sonner", () => mockSonner());
vi.mock("@/providers/auth-provider", () => mockAuth());
vi.mock("@/lib/api/codebase", () => ({
  codebaseApi: {
    list: mocks.listCodebases,
    listVersions: mocks.listVersions,
    createBuild: mocks.createBuild,
    createSnapshot: mocks.createSnapshot,
    retryBuild: vi.fn(),
    cancelBuild: vi.fn(),
    ingestStream: vi.fn(() => vi.fn()),
    delete: vi.fn(),
  },
}));
vi.mock("@/lib/api/session", () => ({
  sessionApi: { createSession: mocks.createSession },
}));
vi.mock("@/components/codebase/create-codebase-dialog", () => ({
  CreateCodebaseDialog: () => null,
}));
vi.mock("@/components/confirm-delete-dialog", () => ({
  ConfirmDeleteDialog: () => null,
}));
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
  IconCodebase: () => null,
  IconDelete: () => null,
  IconDownload: () => null,
  IconLoading: () => null,
  IconMore: () => null,
  IconRefresh: () => null,
}));

import { CodebaseLibrary } from "./codebase-library";

function findButton(container: HTMLDivElement, label: string): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll("button")).find(
    (candidate) => candidate.textContent?.trim() === label,
  );
  expect(button).toBeDefined();
  return button as HTMLButtonElement;
}

async function renderLibrary() {
  const { container, root, unmount } = await renderComponent(<CodebaseLibrary />);
  await Promise.resolve();
  await Promise.resolve();
  await new Promise((resolve) => window.setTimeout(resolve, 0));
  return { container, root, unmount };
}

describe("codebase library versions", () => {
  afterEach(() => {
    mocks.createSession.mockReset();
    mocks.createBuild.mockReset();
    mocks.createSnapshot.mockReset();
    mocks.listCodebases.mockReset();
    mocks.listVersions.mockReset();
    mocks.push.mockReset();
    vi.useRealTimers();
    document.body.replaceChildren();
  });

  it("keeps Ask and Agent enabled during candidate rebuilds and starts with displayed active version", async () => {
    mocks.listCodebases.mockResolvedValue({
      codebases: [
        {
          id: "cb1",
          name: "Repo",
          source_type: "files",
          status: "ready",
          file_count: 7,
          active_version_id: "active-v2",
          ingest_task_id: "build-candidate",
        },
      ],
    });
    mocks.listVersions.mockResolvedValue({
      codebase_id: "cb1",
      active_version_id: "active-v2",
      active_build: {
        id: "build-candidate",
        codebase_id: "cb1",
        version_id: "candidate",
        parent_version_id: "active-v2",
        command_key: "reanalyze:cb1",
        state: "running",
        phase: "analyze",
        progress: 0.5,
        capabilities: [],
        degraded_reasons: [],
        metrics: {},
        last_event_seq: 1,
        created_at: "2026-07-30T00:00:00Z",
        can_retry: false,
        can_cancel: true,
      },
      versions: [
        {
          id: "active-v2",
          codebase_id: "cb1",
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
    });
    mocks.createSession.mockResolvedValue({ session_id: "session-agent" });

    const { container, root } = await renderLibrary();
    expect(findButton(container, "Ask").disabled).toBe(false);
    expect(findButton(container, "Agent").disabled).toBe(false);
    expect(findButton(container, "View build").disabled).toBe(false);

    act(() => {
      findButton(container, "Agent").click();
    });
    await Promise.resolve();
    await Promise.resolve();

    expect(mocks.createSession).toHaveBeenCalledWith({
      codebase_id: "cb1",
      codebase_version_id: "active-v2",
      mode: "agent",
    });
    expect(mocks.createSnapshot).not.toHaveBeenCalled();

    root.unmount();
  });

  it("creates a snapshot through the mutating snapshots endpoint", async () => {
    mocks.listCodebases.mockResolvedValue({
      codebases: [
        {
          id: "cb1",
          name: "Repo",
          source_type: "files",
          status: "ready",
          file_count: 7,
          active_version_id: "active-v2",
        },
      ],
    });
    mocks.listVersions.mockResolvedValue({
      codebase_id: "cb1",
      active_version_id: "active-v2",
      active_build: null,
      versions: [],
    });
    mocks.createSnapshot.mockResolvedValue({ snapshot_key: "codebases/cb1/download.tgz" });

    const { container, root } = await renderLibrary();

    await act(async () => {
      findButton(container, "Download").click();
    });

    expect(mocks.createSnapshot).toHaveBeenCalledTimes(1);
    expect(mocks.createSnapshot).toHaveBeenCalledWith("cb1");

    root.unmount();
  });
});
