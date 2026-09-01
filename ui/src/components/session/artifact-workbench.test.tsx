// @vitest-environment jsdom

import { NextIntlClientProvider } from "next-intl";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ArtifactEventSummary } from "@/lib/api/types";

import { renderComponent } from "@/test-utils/render";

import en from "../../../messages/en.json";

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

    expect(mocks.get).toHaveBeenCalledWith("art-1");
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
