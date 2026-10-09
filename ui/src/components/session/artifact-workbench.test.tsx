// @vitest-environment jsdom

import { act } from "react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";

import { executionViewApi } from "@/lib/api/execution-view";
import { ApiError } from "@/lib/api/fetch";
import type { ArtifactEventSummary } from "@/lib/api/types";

import { renderComponent } from "@/test-utils/render";

import en from "../../../messages/en.json";

const auth = vi.hoisted(() => ({ revision: 1 }));
vi.mock("@/providers/auth-provider", () => ({
  useAuth: () => ({ user: { id: "u" }, loading: false }),
}));
vi.mock("@/providers/client-data-provider", () => ({
  useClientDataScope: () => ({
    scope: { userId: "u", workspaceId: "w" },
    scopeRevision: auth.revision,
  }),
}));
const mocks = vi.hoisted(() => ({
  get: vi.fn(),
  getContent: vi.fn(),
  share: vi.fn(),
  revokeShare: vi.fn(),
}));

vi.mock("@/lib/api/artifacts", () => ({
  artifactsApi: {
    get: mocks.get,
    getContent: mocks.getContent,
    share: mocks.share,
    revokeShare: mocks.revokeShare,
  },
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

import { ArtifactWorkbench } from "./artifact-workbench";

const ARTIFACT: ArtifactEventSummary = {
  artifact_id: "art-1",
  kind: "doc",
  title: "Report",
  status: "final",
  storage_ref: "ref",
  version: 1,
};

const BASE_DETAIL = {
  id: "art-1",
  session_id: "sess-1234",
  kind: "doc" as const,
  title: "Report",
  storage_ref: "ref",
  version_refs: ["v1"],
  status: "final" as const,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

function renderWorkbench() {
  return renderComponent(
    <NextIntlClientProvider locale="en" messages={en}>
      <ArtifactWorkbench sessionId="sess-1234" artifacts={[ARTIFACT]} />
    </NextIntlClientProvider>,
  );
}

afterEach(() => {
  document.body.replaceChildren();
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("ArtifactWorkbench share status", () => {
  it("shows persistent shared status, expiry hint, token suffix and revoke button from backend detail", async () => {
    mocks.get.mockResolvedValue({
      ...BASE_DETAIL,
      is_shared: true,
      share_expires_at: "2026-09-10T00:00:00Z",
      share_token_preview: "ab12",
    });
    mocks.getContent.mockResolvedValue({
      content: "# Hi",
      content_type: "text/markdown",
      incomplete: false,
    });

    const { container, unmount } = await renderWorkbench();

    expect(mocks.get).toHaveBeenCalledWith("art-1", expect.objectContaining({ workspaceId: "w" }));
    expect(container.textContent).toContain(en.artifactWorkbench.sharedActive);
    expect(container.textContent).toContain("ab12");
    expect(container.textContent).toContain(en.artifactWorkbench.revokeShare);
    await unmount();
  });

  it("does not show shared status or revoke button when the artifact is not shared", async () => {
    mocks.get.mockResolvedValue({
      ...BASE_DETAIL,
      is_shared: false,
      share_expires_at: null,
      share_token_preview: null,
    });
    mocks.getContent.mockResolvedValue({
      content: "# Hi",
      content_type: "text/markdown",
      incomplete: false,
    });

    const { container, unmount } = await renderWorkbench();

    expect(container.textContent).not.toContain(en.artifactWorkbench.sharedActive);
    expect(container.textContent).not.toContain(en.artifactWorkbench.revokeShare);
    // The share action itself stays available.
    expect(container.textContent).toContain(en.artifactWorkbench.share);
    await unmount();
  });
});

it("controlled immutable version survives refreshed latest metadata", async () => {
  mocks.get.mockResolvedValue({ ...BASE_DETAIL, is_shared: false });
  mocks.getContent.mockImplementation((_id: string, v: number) =>
    Promise.resolve({ content: `fixed-${v}`, content_type: "text/plain" }),
  );
  const wrap = (latest: number) => (
    <NextIntlClientProvider locale="en" messages={en}>
      <ArtifactWorkbench
        sessionId="sess"
        artifacts={[{ ...ARTIFACT, version: latest }]}
        version={1}
        onVersionChange={() => {}}
      />
    </NextIntlClientProvider>
  );
  const { container, root, unmount } = await renderComponent(wrap(2));
  expect(container.textContent).toContain("fixed-1");
  await act(async () => {
    root.render(wrap(3));
  });
  expect(container.textContent).toContain("fixed-1");
  expect(container.textContent).not.toContain("fixed-3");
  await unmount();
});

it("scope identity replacement removes old preview before late body response", async () => {
  let resolve!: (v: unknown) => void;
  mocks.get.mockResolvedValue({ ...BASE_DETAIL, is_shared: false });
  mocks.getContent
    .mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolve = r;
        }),
    )
    .mockResolvedValue({ content: "new authority", content_type: "text/plain" });
  const { container, root, unmount } = await renderWorkbench();
  auth.revision++;
  await act(async () => {
    root.render(
      <NextIntlClientProvider locale="en" messages={en}>
        <ArtifactWorkbench sessionId="sess-1234" artifacts={[ARTIFACT]} />
      </NextIntlClientProvider>,
    );
  });
  await act(async () => resolve({ content: "old secret", content_type: "text/plain" }));
  expect(container.textContent).toContain("new authority");
  expect(container.textContent).not.toContain("old secret");
  await unmount();
});

it("controlled version options exclude unproven future versions and emit an intentional version choice", async () => {
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: vi.fn(),
  });
  mocks.get.mockResolvedValue({ ...BASE_DETAIL, is_shared: false });
  mocks.getContent.mockResolvedValue({ content: "fixed", content_type: "text/plain" });
  const change = vi.fn();
  const { container, unmount } = await renderComponent(
    <NextIntlClientProvider locale="en" messages={en}>
      <ArtifactWorkbench
        sessionId="s"
        artifacts={[{ ...ARTIFACT, version: 4 }]}
        version={1}
        visibleVersions={[1, 2]}
        onVersionChange={change}
      />
    </NextIntlClientProvider>,
  );
  await act(async () => {
    container
      .querySelectorAll('[role="combobox"]')[1]
      .dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  });
  const options = Array.from(document.querySelectorAll('[role="option"]'));
  expect(options.map((option) => option.textContent)).toEqual(["v1", "v2"]);
  await act(async () => {
    options[1].dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  });
  expect(change).toHaveBeenCalledWith(2);
  await unmount();
});

it("legacy export downloads complete fixed original bytes through the authorized reader", async () => {
  mocks.get.mockResolvedValue({ ...BASE_DETAIL, is_shared: false });
  mocks.getContent.mockResolvedValue({ content: "sanitized preview", content_type: "text/html" });
  const original = new Blob(["original full body"]);
  const download = vi.spyOn(executionViewApi, "downloadArtifact").mockResolvedValue(original);
  const create = vi.fn(() => "blob:fixed");
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: create });
  Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  const { container, unmount } = await renderWorkbench();
  await act(async () => {
    Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent === en.artifactWorkbench.export)!
      .click();
  });
  expect(download).toHaveBeenCalledWith(
    "art-1",
    expect.objectContaining({ version: 1, presentation: false }),
    expect.objectContaining({ workspaceId: "w" }),
  );
  expect(create).toHaveBeenCalledWith(original);
  await unmount();
});

it("unavailable original download clears the already authorized legacy preview", async () => {
  mocks.get.mockResolvedValue({ ...BASE_DETAIL, is_shared: false });
  mocks.getContent.mockResolvedValue({ content: "PRIVATE PREVIEW", content_type: "text/plain" });
  vi.spyOn(executionViewApi, "downloadArtifact").mockRejectedValue(
    new ApiError(409, "resource_unavailable", { code: "resource_unavailable" }),
  );
  const { container, unmount } = await renderWorkbench();
  await act(async () => {
    Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent === en.artifactWorkbench.export)!
      .click();
  });
  expect(container.textContent).not.toContain("PRIVATE PREVIEW");
  await unmount();
});

it.each(
  [401, 403, 404, 409].flatMap((status) =>
    ["share", "revoke"].map((action) => ({ status, action })),
  ),
)(
  "$action denial $status destroys the loaded preview and share state",
  async ({ status, action }) => {
    mocks.get.mockResolvedValue({
      ...BASE_DETAIL,
      is_shared: true,
      share_token_preview: "private-token",
    });
    mocks.getContent.mockResolvedValue({ content: "PRIVATE PREVIEW", content_type: "text/plain" });
    const failure = new ApiError(
      status,
      "denied",
      status === 409 ? { code: "resource_unavailable" } : undefined,
    );
    mocks.share.mockRejectedValue(failure);
    mocks.revokeShare.mockRejectedValue(failure);
    const { container, unmount } = await renderWorkbench();
    expect(container.textContent).toContain("PRIVATE PREVIEW");
    await act(async () => {
      Array.from(container.querySelectorAll("button"))
        .find(
          (button) =>
            button.textContent ===
            (action === "share" ? en.artifactWorkbench.reshare : en.artifactWorkbench.revokeShare),
        )!
        .click();
    });
    expect(container.textContent).not.toContain("PRIVATE PREVIEW");
    expect(container.textContent).not.toContain("private-token");
    expect(container.textContent).toContain(en.artifactWorkbench.loadFailed);
    await unmount();
  },
);

it("metadata denial aborts pending body and prevents late body cache writes", async () => {
  let rejectMetadata!: (reason: unknown) => void;
  let resolveBody!: (body: unknown) => void;
  mocks.get.mockImplementation(
    () =>
      new Promise((_, reject) => {
        rejectMetadata = reject;
      }),
  );
  mocks.getContent.mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveBody = resolve;
      }),
  );
  vi.spyOn(executionViewApi, "downloadArtifact").mockRejectedValue(new ApiError(403, "denied"));
  const { container, unmount } = await renderWorkbench();
  const signal = mocks.getContent.mock.calls.at(-1)![2].signal;
  await act(async () => {
    rejectMetadata(new ApiError(403, "denied"));
  });
  expect(signal.aborted).toBe(true);
  // A transport ignoring AbortSignal still must not be consumed into state.
  const consume = vi.fn(() => "LATE PRIVATE BODY");
  await act(async () =>
    resolveBody({
      get content() {
        return consume();
      },
      content_type: "text/plain",
    }),
  );
  expect(consume).not.toHaveBeenCalled();
  expect(container.textContent).not.toContain("LATE PRIVATE BODY");
  await unmount();
});

it("share denial aborts an in-flight original export and prevents its late URL creation", async () => {
  mocks.get.mockResolvedValue({ ...BASE_DETAIL, is_shared: false });
  mocks.getContent.mockResolvedValue({ content: "PRIVATE PREVIEW", content_type: "text/plain" });
  mocks.share.mockRejectedValue(new ApiError(403, "denied"));
  let resolveDownload!: (blob: Blob) => void;
  const download = vi.spyOn(executionViewApi, "downloadArtifact").mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveDownload = resolve;
      }),
  );
  const create = vi.fn();
  Object.defineProperty(URL, "createObjectURL", { configurable: true, value: create });
  const { container, unmount } = await renderWorkbench();
  await act(async () =>
    Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent === en.artifactWorkbench.export)!
      .click(),
  );
  const signal = download.mock.calls[0][2]!.signal;
  await act(async () =>
    Array.from(container.querySelectorAll("button"))
      .find((button) => button.textContent === en.artifactWorkbench.share)!
      .click(),
  );
  expect(signal!.aborted).toBe(true);
  await act(async () => resolveDownload(new Blob(["private original"])));
  expect(create).not.toHaveBeenCalled();
  expect(container.textContent).not.toContain("PRIVATE PREVIEW");
  await unmount();
});

it("body denial aborts metadata and prevents its late share-state commit", async () => {
  let resolveMetadata!: (value: unknown) => void;
  mocks.get.mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveMetadata = resolve;
      }),
  );
  mocks.getContent.mockRejectedValue(new ApiError(403, "denied"));
  const { container, unmount } = await renderWorkbench();
  expect(mocks.get.mock.calls.at(-1)![1].signal.aborted).toBe(true);
  const consume = vi.fn(() => true);
  await act(async () =>
    resolveMetadata({
      ...BASE_DETAIL,
      get is_shared() {
        return consume();
      },
      share_token_preview: "late secret",
    }),
  );
  expect(consume).not.toHaveBeenCalled();
  expect(container.textContent).not.toContain("late secret");
  await unmount();
});
