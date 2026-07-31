// @vitest-environment jsdom

import { act } from "react";
import { NextIntlClientProvider } from "next-intl";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import enMessages from "../../../messages/en.json";

const mocks = vi.hoisted(() => ({
  listVersions: vi.fn(),
  createBuild: vi.fn(),
  retryBuild: vi.fn(),
  cancelBuild: vi.fn(),
}));

vi.mock("@/lib/api/codebase", () => ({
  codebaseApi: {
    listVersions: mocks.listVersions,
    createBuild: mocks.createBuild,
    retryBuild: mocks.retryBuild,
    cancelBuild: mocks.cancelBuild,
  },
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));
vi.mock("@/lib/icons", () => ({ IconLoading: () => <span>loading</span> }));

import { CodebaseVersionStatus } from "./codebase-version-status";

const history = {
  codebase_id: "cb1",
  active_version_id: "active",
  active_build: {
    id: "build-1",
    codebase_id: "cb1",
    version_id: "candidate",
    parent_version_id: "active",
    command_key: "reanalyze:cb1",
    state: "running",
    phase: "artifacts",
    progress: 0.75,
    capabilities: [],
    degraded_reasons: [],
    metrics: {},
    heartbeat_at: "2026-07-30T00:00:30Z",
    last_event_seq: 2,
    created_at: "2026-07-30T00:00:00Z",
    can_retry: false,
    can_cancel: true,
  },
  versions: [
    {
      id: "active",
      codebase_id: "cb1",
      state: "ready",
      capabilities: {
        lexical_search: true,
        vector_search: false,
        flowchart: false,
      },
      degraded_reasons: ["EMBEDDING_UNAVAILABLE"],
      metrics: {
        unsupported_views: {
          flowchart: "unsupported",
        },
      },
      legacy_snapshot: false,
      created_at: "2026-07-29T00:00:00Z",
      published_at: "2026-07-29T00:01:00Z",
      is_active: true,
      is_published: true,
      is_candidate: false,
    },
  ],
};

async function renderStatus() {
  mocks.listVersions.mockResolvedValue(history);
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      <NextIntlClientProvider locale="en" messages={enMessages}>
        <CodebaseVersionStatus codebaseId="cb1" />
      </NextIntlClientProvider>,
    );
    await Promise.resolve();
  });
  return { container, root };
}

describe("CodebaseVersionStatus", () => {
  beforeAll(() => {
    (
      globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
    ).IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterEach(() => {
    mocks.listVersions.mockReset();
    mocks.createBuild.mockReset();
    mocks.retryBuild.mockReset();
    mocks.cancelBuild.mockReset();
    document.body.replaceChildren();
  });

  it("renders active version, candidate build, degradation, and unsupported artifact reasons", async () => {
    const { container, root } = await renderStatus();

    expect(container.textContent).toContain("Active version: active");
    expect(container.textContent).toContain("Candidate build running · artifacts · 75%");
    expect(container.textContent).toContain("Degraded: EMBEDDING_UNAVAILABLE");
    expect(container.textContent).toContain("Unsupported views: flowchart: unsupported");
    expect(container.textContent).toContain("Cancel");

    await act(async () => root.unmount());
  });
});
