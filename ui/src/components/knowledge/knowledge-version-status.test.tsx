// @vitest-environment jsdom

import { act } from "react";
import { NextIntlClientProvider } from "next-intl";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

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
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));
vi.mock("@/lib/icons", () => ({ IconLoading: () => <span>loading</span> }));

import { KnowledgeVersionStatus } from "./knowledge-version-status";

const history = {
  knowledge_base_id: "kb1",
  active_version_id: "v-published",
  active_build: {
    id: "build-candidate",
    knowledge_base_id: "kb1",
    version_id: "v-candidate",
    parent_version_id: "v-published",
    command_key: "command",
    state: "running",
    phase: "chunk",
    progress: 0.5,
    capabilities: [],
    degraded_reasons: [],
    metrics: {},
    heartbeat_at: "2026-07-30T00:00:30Z",
    last_event_seq: 1,
    created_at: "2026-07-30T00:00:00Z",
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
      legacy_snapshot: false,
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
      legacy_snapshot: false,
      created_at: "2026-07-28T00:00:00Z",
      published_at: "2026-07-28T00:01:00Z",
      is_active: false,
      is_published: true,
      is_candidate: false,
    },
  ],
};

async function renderLocale(locale: "en" | "zh", response: typeof history | null = history) {
  if (response) mocks.listVersions.mockResolvedValue(response);
  const container = document.createElement("div");
  document.body.append(container);
  const root = createRoot(container);
  await act(async () => {
    root.render(
      <NextIntlClientProvider locale={locale} messages={locale === "en" ? enMessages : zhMessages}>
        <KnowledgeVersionStatus knowledgeBaseId="kb1" />
      </NextIntlClientProvider>,
    );
    await Promise.resolve();
  });
  return { container, root };
}

describe("KnowledgeVersionStatus localization", () => {
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
    vi.useRealTimers();
    document.body.replaceChildren();
  });

  it.each([
    [
      "en",
      "Active version: v-published",
      "Cancel",
      "Capabilities: keyword_search: available, graph_search: unavailable",
      "View version v-old",
      "Heartbeat: 2026-07-30T00:00:30Z",
    ],
    [
      "zh",
      "当前版本：v-published",
      "取消",
      "能力：keyword_search: 可用, graph_search: 不可用",
      "查看版本 v-old",
      "心跳：2026-07-30T00:00:30Z",
    ],
  ] as const)(
    "renders real %s messages",
    async (locale, activeText, cancelText, capabilitiesText, historyText, heartbeatText) => {
      const { container, root } = await renderLocale(locale);
      expect(container.textContent).toContain(activeText);
      expect(container.textContent).toContain(cancelText);
      expect(container.textContent).toContain(capabilitiesText);
      expect(container.textContent).toContain(historyText);
      expect(container.textContent).toContain(heartbeatText);
      await act(async () => {
        Array.from(container.querySelectorAll("button"))
          .find((button) => button.textContent === historyText)
          ?.click();
      });
      expect(container.textContent).toContain("v-old");
      await act(async () => root.unmount());
    },
  );

  it("polls an active build through progress and terminal retry state", async () => {
    vi.useFakeTimers();
    const running = {
      ...history,
      active_build: {
        ...history.active_build,
        progress: 0.1,
        last_event_seq: 1,
      },
      versions: history.versions.slice(0, 1),
    };
    const progressed = {
      ...running,
      active_build: {
        ...running.active_build,
        phase: "graph",
        progress: 0.7,
        last_event_seq: 2,
      },
    };
    const terminal = {
      knowledge_base_id: "kb1",
      active_version_id: "v2",
      active_build: null,
      versions: [
        {
          ...history.versions[0],
          id: "v2",
          is_active: true,
        },
        {
          id: "v-candidate",
          knowledge_base_id: "kb1",
          state: "failed",
          capabilities: {},
          degraded_reasons: [],
          metrics: {},
          legacy_snapshot: false,
          created_at: "2026-07-30T00:00:00Z",
          is_active: false,
          is_published: false,
          is_candidate: true,
          build: {
            ...history.active_build,
            state: "failed",
            phase: "graph",
            progress: 0.7,
            last_event_seq: 3,
            can_retry: true,
            can_cancel: false,
            error_message: "graph failed",
          },
        },
      ],
    };
    mocks.listVersions
      .mockResolvedValueOnce(running)
      .mockResolvedValueOnce(progressed)
      .mockResolvedValueOnce(terminal);

    const { container, root } = await renderLocale("en", null);
    expect(container.textContent).toContain("10%");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(container.textContent).toContain("graph");
    expect(container.textContent).toContain("70%");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(container.textContent).toContain("Active version: v2");
    expect(container.textContent).toContain("graph failed");
    expect(container.textContent).toContain("Retry");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(mocks.listVersions).toHaveBeenCalledTimes(3);
    await act(async () => root.unmount());
  });
});
