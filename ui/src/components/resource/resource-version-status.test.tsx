// @vitest-environment jsdom

import { act } from "react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockSonner } from "@/test-utils/mocks";
import { renderComponent } from "@/test-utils/render";

import enMessages from "../../../messages/en.json";
import zhMessages from "../../../messages/zh.json";

const mocks = vi.hoisted(() => ({
  listVersions: vi.fn(),
  createBuild: vi.fn(),
  retryBuild: vi.fn(),
  cancelBuild: vi.fn(),
}));

vi.mock("@/lib/api/knowledge", () => ({
  knowledgeApi: {
    listVersions: mocks.listVersions,
    createBuild: mocks.createBuild,
    retryBuild: mocks.retryBuild,
    cancelBuild: mocks.cancelBuild,
  },
}));
vi.mock("sonner", () => mockSonner());
vi.mock("@/lib/icons", () => ({ IconLoading: () => <span>loading</span> }));

import { knowledgeApi } from "@/lib/api/knowledge";

import { ResourceVersionStatus } from "./resource-version-status";

function makeHistory() {
  return {
    knowledge_base_id: "kb1",
    active_version_id: "v-published",
    active_build: {
      id: "build-candidate",
      run_id: "run-candidate",
      knowledge_base_id: "kb1",
      version_id: "v-candidate",
      status: "running",
      phase: "chunk",
      progress: 50,
      created_at: "2026-07-30T00:00:00Z",
      updated_at: "2026-07-30T00:00:30Z",
      terminal_at: null,
      failure_code: null,
      can_retry: false,
      can_cancel: true,
    },
    versions: [
      {
        id: "v-published",
        knowledge_base_id: "kb1",
        state: "ready",
        capabilities: { keyword_search: true, graph_search: false },
        degraded_reasons: [],
        metrics: {},
        created_at: "2026-07-29T00:00:00Z",
        published_at: "2026-07-29T00:01:00Z",
        is_active: true,
        is_published: true,
        is_candidate: false,
      },
      {
        id: "v-old",
        knowledge_base_id: "kb1",
        state: "ready",
        capabilities: { keyword_search: true },
        degraded_reasons: [],
        metrics: {},
        created_at: "2026-07-28T00:00:00Z",
        published_at: "2026-07-28T00:01:00Z",
        is_active: false,
        is_published: true,
        is_candidate: false,
      },
    ],
  };
}

async function renderStatus(locale: "en" | "zh" = "en") {
  return renderComponent(
    <NextIntlClientProvider locale={locale} messages={locale === "en" ? enMessages : zhMessages}>
      <ResourceVersionStatus api={knowledgeApi} resourceId="kb1" />
    </NextIntlClientProvider>,
  );
}

describe("ResourceVersionStatus", () => {
  afterEach(() => {
    vi.clearAllMocks();
    document.body.replaceChildren();
  });

  it("renders the active build, capabilities, and version history", async () => {
    mocks.listVersions.mockResolvedValue(makeHistory());
    const { container, unmount } = await renderStatus();

    expect(container.textContent).toContain("Active version: v-published");
    expect(container.textContent).toContain("running");
    expect(container.textContent).toContain("chunk");
    expect(container.textContent).toContain("50%");
    expect(container.textContent).toContain(
      "Capabilities: keyword_search: available, graph_search: unavailable",
    );

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent === "View version v-old")
        ?.click();
    });
    expect(container.textContent).toContain("Viewing historical version: v-old");
    await unmount();
  });

  it("uses localized knowledge-base messages", async () => {
    mocks.listVersions.mockResolvedValue(makeHistory());
    const { container, unmount } = await renderStatus("zh");

    expect(container.textContent).toContain("当前版本：v-published");
    expect(container.textContent).toContain("取消");
    expect(container.textContent).toContain("能力：keyword_search: 可用, graph_search: 不可用");
    await unmount();
  });

  it("creates a new build when no build is active", async () => {
    const history = { ...makeHistory(), active_build: null };
    mocks.listVersions.mockResolvedValue(history);
    mocks.createBuild.mockResolvedValue({});
    const { container, unmount } = await renderStatus();
    const reindexButton = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent === "Re-index",
    );

    await act(async () => {
      reindexButton?.click();
      await Promise.resolve();
    });
    expect(mocks.createBuild).toHaveBeenCalledWith("kb1");
    await unmount();
  });
});
