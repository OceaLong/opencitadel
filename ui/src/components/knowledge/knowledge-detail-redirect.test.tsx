// @vitest-environment jsdom

import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockNavigation, mockNextIntl, mockSonner } from "@/test-utils/mocks";
import { renderComponent } from "@/test-utils/render";

const mocks = vi.hoisted(() => ({
  getKnowledgeBase: vi.fn(),
  createSession: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => mockNavigation({ replace: mocks.replace }));
vi.mock("next-intl", () => mockNextIntl());
vi.mock("sonner", () => mockSonner());
vi.mock("@/lib/api/knowledge", () => ({
  knowledgeApi: { get: mocks.getKnowledgeBase },
}));
vi.mock("@/lib/api/session", () => ({
  sessionApi: { createSession: mocks.createSession },
}));
vi.mock("@/lib/icons", () => ({ IconLoading: () => null }));

import { KnowledgeDetailRedirect } from "./knowledge-detail-redirect";

describe("KnowledgeDetailRedirect", () => {
  afterEach(() => {
    mocks.getKnowledgeBase.mockReset();
    mocks.createSession.mockReset();
    mocks.replace.mockReset();
    document.body.replaceChildren();
  });

  it("creates Ask with the exact active published version", async () => {
    mocks.getKnowledgeBase.mockResolvedValue({
      id: "kb1",
      active_version_id: "v-active",
    });
    mocks.createSession.mockResolvedValue({ session_id: "s1" });

    const { unmount } = await renderComponent(<KnowledgeDetailRedirect knowledgeBaseId="kb1" />);
    await act(async () => {
      await Promise.resolve();
    });

    expect(mocks.createSession).toHaveBeenCalledWith({
      knowledge_base_id: "kb1",
      knowledge_base_version_id: "v-active",
      mode: "ask",
    });
    expect(mocks.replace).toHaveBeenCalledWith("/sessions/s1");
    await unmount();
  });
});
