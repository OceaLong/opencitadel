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
vi.mock("@/lib/api/codebase", () => ({
  codebaseApi: {
    listVersions: mocks.listVersions,
    createBuild: mocks.createBuild,
    retryBuild: mocks.retryBuild,
    cancelBuild: mocks.cancelBuild,
  },
}));
vi.mock("sonner", () => mockSonner());
vi.mock("@/lib/icons", () => ({ IconLoading: () => <span>loading</span> }));

import { codebaseApi } from "@/lib/api/codebase";
import { knowledgeApi } from "@/lib/api/knowledge";

import { ResourceVersionStatus } from "./resource-version-status";

function resetMocks() {
  mocks.listVersions.mockReset();
  mocks.createBuild.mockReset();
  mocks.retryBuild.mockReset();
  mocks.cancelBuild.mockReset();
}

/**
 * Shared version-history fixture shape. Only the envelope id field name
 * (`knowledge_base_id` vs `codebase_id`) differs between the two resources
 * -- the component never renders it -- so the rest of the fixture (version
 * ids, build shape, capability names) is identical across both namespaces,
 * letting the `describe.each` below assert on the same literal output.
 */
function makeHistory(envelopeIdKey: "knowledge_base_id" | "codebase_id", envelopeId: string) {
  return {
    [envelopeIdKey]: envelopeId,
    active_version_id: "v-published",
    active_build: {
      id: "build-candidate",
      run_id: "run-candidate",
      [envelopeIdKey]: envelopeId,
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
        [envelopeIdKey]: envelopeId,
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
        [envelopeIdKey]: envelopeId,
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

/** Running -> progressed -> terminal(retry-able) sequence, mirroring the
 * three `listVersions` responses a live poll loop would see. */
function makePollSequence(envelopeIdKey: "knowledge_base_id" | "codebase_id", envelopeId: string) {
  const base = makeHistory(envelopeIdKey, envelopeId);
  const running = {
    ...base,
    active_build: { ...base.active_build, progress: 10 },
    versions: base.versions.slice(0, 1),
  };
  const progressed = {
    ...running,
    active_build: { ...running.active_build, phase: "graph", progress: 70 },
  };
  const terminal = {
    [envelopeIdKey]: envelopeId,
    active_version_id: "v2",
    active_build: null,
    versions: [
      { ...base.versions[0], id: "v2", is_active: true },
      {
        id: "v-candidate",
        [envelopeIdKey]: envelopeId,
        state: "failed",
        capabilities: {},
        degraded_reasons: [],
        metrics: {},
        created_at: "2026-07-30T00:00:00Z",
        is_active: false,
        is_published: false,
        is_candidate: true,
        build: {
          ...base.active_build,
          status: "failed",
          phase: "graph",
          progress: 70,
          can_retry: true,
          can_cancel: false,
          failure_code: "GRAPH_BUILD_FAILED",
          terminal_at: "2026-07-30T00:01:00Z",
        },
      },
    ],
  };
  return { running, progressed, terminal };
}

const CASES = [
  {
    ns: "knowledge" as const,
    resourceId: "kb1",
    history: makeHistory("knowledge_base_id", "kb1"),
  },
  {
    ns: "codebase" as const,
    resourceId: "cb1",
    history: makeHistory("codebase_id", "cb1"),
  },
];

/**
 * Renders `ResourceVersionStatus` for the given case. Branches on the
 * literal `ns` (rather than looking up `api` from a pre-built table) so
 * each JSX call site sees the concrete `knowledgeApi`/`codebaseApi` const --
 * `ResourceVersionStatus`'s generics can't be inferred from a value typed
 * as the *union* of both resources' api shapes (TS picks one branch and
 * rejects the other), so `CASES` intentionally carries only the
 * ns-determined, non-generic bits (`ns`/`resourceId`/`history`).
 */
function renderStatus(ns: "knowledge" | "codebase", resourceId: string) {
  const element =
    ns === "knowledge" ? (
      <ResourceVersionStatus api={knowledgeApi} resourceId={resourceId} ns="knowledge" />
    ) : (
      <ResourceVersionStatus api={codebaseApi} resourceId={resourceId} ns="codebase" />
    );
  return renderComponent(
    <NextIntlClientProvider locale="en" messages={enMessages}>
      {element}
    </NextIntlClientProvider>,
  );
}

describe.each(CASES)("ResourceVersionStatus ($ns)", ({ ns, resourceId, history }) => {
  afterEach(() => {
    resetMocks();
    vi.useRealTimers();
    document.body.replaceChildren();
  });

  it("renders active version, running candidate build, capabilities, and historical versions", async () => {
    mocks.listVersions.mockResolvedValue(history);
    const { container, unmount } = await renderStatus(ns, resourceId);

    const text = container.textContent ?? "";
    expect(text).toContain("Active version: v-published");
    expect(text).toContain("running");
    expect(text).toContain("chunk");
    expect(text).toContain("50%");
    expect(text).toContain("Capabilities: keyword_search: available, graph_search: unavailable");
    expect(text).toContain("Cancel");

    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find((button) => button.textContent === "View version v-old")
        ?.click();
    });
    expect(container.textContent).toContain("v-old");
    await unmount();
  });

  it("polls an active build through progress and a terminal retry-able state", async () => {
    vi.useFakeTimers();
    const { running, progressed, terminal } = makePollSequence(
      ns === "knowledge" ? "knowledge_base_id" : "codebase_id",
      resourceId,
    );
    mocks.listVersions
      .mockResolvedValueOnce(running)
      .mockResolvedValueOnce(progressed)
      .mockResolvedValueOnce(terminal);

    const { container, unmount } = await renderStatus(ns, resourceId);
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
    expect(container.textContent).toContain("GRAPH_BUILD_FAILED");
    expect(container.textContent).toContain("Retry");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(mocks.listVersions).toHaveBeenCalledTimes(3);
    await unmount();
  });
});

// --- Namespace-specific slots ("3 real differences" beyond ns/api/resourceId) ---

describe("ResourceVersionStatus (knowledge) localization", () => {
  afterEach(() => {
    resetMocks();
    document.body.replaceChildren();
  });

  async function renderLocale(locale: "en" | "zh") {
    mocks.listVersions.mockResolvedValue(makeHistory("knowledge_base_id", "kb1"));
    return renderComponent(
      <NextIntlClientProvider locale={locale} messages={locale === "en" ? enMessages : zhMessages}>
        <ResourceVersionStatus api={knowledgeApi} resourceId="kb1" ns="knowledge" />
      </NextIntlClientProvider>,
    );
  }

  it.each([
    [
      "en",
      "Active version: v-published",
      "Cancel",
      "Capabilities: keyword_search: available, graph_search: unavailable",
      "View version v-old",
    ],
    [
      "zh",
      "当前版本：v-published",
      "取消",
      "能力：keyword_search: 可用, graph_search: 不可用",
      "查看版本 v-old",
    ],
  ] as const)(
    "renders real %s messages",
    async (locale, activeText, cancelText, capabilitiesText, historyText) => {
      const { container, unmount } = await renderLocale(locale);
      expect(container.textContent).toContain(activeText);
      expect(container.textContent).toContain(cancelText);
      expect(container.textContent).toContain(capabilitiesText);
      expect(container.textContent).toContain(historyText);
      await act(async () => {
        Array.from(container.querySelectorAll("button"))
          .find((button) => button.textContent === historyText)
          ?.click();
      });
      expect(container.textContent).toContain("v-old");
      await unmount();
    },
  );

  it("shows a create-build (reindex) action absent from the codebase namespace", async () => {
    mocks.listVersions.mockResolvedValue({
      knowledge_base_id: "kb1",
      active_version_id: "v-published",
      active_build: null,
      versions: [
        {
          id: "v-published",
          knowledge_base_id: "kb1",
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
    const { container, unmount } = await renderComponent(
      <NextIntlClientProvider locale="en" messages={enMessages}>
        <ResourceVersionStatus api={knowledgeApi} resourceId="kb1" ns="knowledge" />
      </NextIntlClientProvider>,
    );
    const reindexButton = Array.from(container.querySelectorAll("button")).find(
      (button) => button.textContent === "Re-index",
    );
    expect(reindexButton).toBeDefined();

    await act(async () => {
      reindexButton?.click();
      await Promise.resolve();
    });
    expect(mocks.createBuild).toHaveBeenCalledWith("kb1");
    await unmount();
  });
});

describe("ResourceVersionStatus (codebase) degraded/unsupported info", () => {
  afterEach(() => {
    resetMocks();
    document.body.replaceChildren();
  });

  it("renders active version, candidate build, degradation, and unsupported artifact reasons", async () => {
    mocks.listVersions.mockResolvedValue({
      codebase_id: "cb1",
      active_version_id: "active",
      active_build: {
        id: "build-1",
        run_id: "run-build-1",
        codebase_id: "cb1",
        version_id: "candidate",
        status: "running",
        phase: "artifacts",
        progress: 75,
        created_at: "2026-07-30T00:00:00Z",
        updated_at: "2026-07-30T00:00:30Z",
        terminal_at: null,
        failure_code: null,
        can_retry: false,
        can_cancel: true,
      },
      versions: [
        {
          id: "active",
          codebase_id: "cb1",
          state: "ready",
          capabilities: { lexical_search: true, vector_search: false, flowchart: false },
          degraded_reasons: ["EMBEDDING_UNAVAILABLE"],
          metrics: { unsupported_views: { flowchart: "unsupported" } },
          created_at: "2026-07-29T00:00:00Z",
          published_at: "2026-07-29T00:01:00Z",
          is_active: true,
          is_published: true,
          is_candidate: false,
        },
      ],
    });

    const { container, unmount } = await renderComponent(
      <NextIntlClientProvider locale="en" messages={enMessages}>
        <ResourceVersionStatus api={codebaseApi} resourceId="cb1" ns="codebase" />
      </NextIntlClientProvider>,
    );

    expect(container.textContent).toContain("Active version: active");
    expect(container.textContent).toContain("Candidate build running · artifacts · 75%");
    expect(container.textContent).toContain("Degraded: EMBEDDING_UNAVAILABLE");
    expect(container.textContent).toContain("Unsupported views: flowchart: unsupported");
    expect(container.textContent).toContain("Cancel");

    await unmount();
  });

  it("never renders a create-build (reindex) action", async () => {
    mocks.listVersions.mockResolvedValue(makeHistory("codebase_id", "cb1"));
    const { container, unmount } = await renderComponent(
      <NextIntlClientProvider locale="en" messages={enMessages}>
        <ResourceVersionStatus api={codebaseApi} resourceId="cb1" ns="codebase" />
      </NextIntlClientProvider>,
    );
    expect(
      Array.from(container.querySelectorAll("button")).some(
        (button) => button.textContent === "Re-index" || button.textContent === "View build",
      ),
    ).toBe(false);
    await unmount();
  });
});
