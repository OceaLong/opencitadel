// @vitest-environment jsdom

import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { mockNavigation, mockNextIntl, mockSonner } from "@/test-utils/mocks";
import { renderComponent } from "@/test-utils/render";

const mocks = vi.hoisted(() => ({
  createSession: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => mockNavigation({ replace: mocks.replace }));
vi.mock("next-intl", () => mockNextIntl());
vi.mock("sonner", () => mockSonner());
vi.mock("@/lib/api/session", () => ({
  sessionApi: { createSession: mocks.createSession },
}));
vi.mock("@/lib/icons", () => ({ IconLoading: () => null }));

import { CodebaseDetailRedirect } from "./codebase-detail-redirect";

describe("CodebaseDetailRedirect", () => {
  afterEach(() => {
    mocks.createSession.mockReset();
    mocks.replace.mockReset();
    document.body.replaceChildren();
  });

  it("creates an Ask session directly (no version gating, unlike knowledge)", async () => {
    mocks.createSession.mockResolvedValue({ session_id: "s1" });

    const { unmount } = await renderComponent(<CodebaseDetailRedirect codebaseId="cb1" />);
    await act(async () => {
      await Promise.resolve();
    });

    expect(mocks.createSession).toHaveBeenCalledWith({ codebase_id: "cb1", mode: "ask" });
    expect(mocks.replace).toHaveBeenCalledWith("/sessions/s1");
    await unmount();
  });

  it("redirects to /codebase and toasts on failure", async () => {
    mocks.createSession.mockRejectedValue(new Error("boom"));

    const { unmount } = await renderComponent(<CodebaseDetailRedirect codebaseId="cb1" />);
    await act(async () => {
      await Promise.resolve();
    });

    expect(mocks.replace).toHaveBeenCalledWith("/codebase");
    await unmount();
  });
});
