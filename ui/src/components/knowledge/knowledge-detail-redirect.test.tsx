// @vitest-environment jsdom

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getKnowledgeBase: vi.fn(),
  createSession: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace }),
}));
vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => key,
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));
vi.mock("@/lib/api/knowledge", () => ({
  knowledgeApi: { get: mocks.getKnowledgeBase },
}));
vi.mock("@/lib/api/session", () => ({
  sessionApi: { createSession: mocks.createSession },
}));
vi.mock("@/lib/icons", () => ({ IconLoading: () => null }));

import { KnowledgeDetailRedirect } from "./knowledge-detail-redirect";

describe("KnowledgeDetailRedirect", () => {
  beforeAll(() => {
    (
      globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }
    ).IS_REACT_ACT_ENVIRONMENT = true;
  });
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
    const container = document.createElement("div");
    document.body.append(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(<KnowledgeDetailRedirect knowledgeBaseId="kb1" />);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mocks.createSession).toHaveBeenCalledWith({
      knowledge_base_id: "kb1",
      knowledge_base_version_id: "v-active",
      mode: "ask",
    });
    expect(mocks.replace).toHaveBeenCalledWith("/sessions/s1");
    await act(async () => root.unmount());
  });
});
