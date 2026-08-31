// @vitest-environment jsdom

import { act, useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderComponent } from "@/test-utils/render";

const mocks = vi.hoisted(() => ({
  bindAuthenticatedUser: vi.fn(),
  clearAuthenticatedData: vi.fn(),
  logout: vi.fn(),
  me: vi.fn(),
}));

vi.mock("@/lib/api/auth", () => ({
  authApi: {
    logout: mocks.logout,
    me: mocks.me,
  },
}));

vi.mock("@/providers/client-data-provider", () => ({
  useClientDataScope: () => ({
    bindAuthenticatedUser: mocks.bindAuthenticatedUser,
    clearAuthenticatedData: mocks.clearAuthenticatedData,
  }),
}));

import { AuthProvider, useAuth } from "./auth-provider";

const USER = {
  id: "user-1",
  email: "user@example.com",
  username: "user",
  display_name: "User",
  avatar_url: "",
  global_role: "user" as const,
  status: "active" as const,
  created_at: "2026-08-28T00:00:00Z",
};

function Probe() {
  const { user, loading } = useAuth();
  useEffect(() => undefined, [user]);
  return <div>{loading ? "loading" : (user?.id ?? "anonymous")}</div>;
}

describe("AuthProvider", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.me.mockResolvedValue(USER);
    mocks.logout.mockResolvedValue(undefined);
  });

  afterEach(() => {
    document.body.replaceChildren();
  });

  it("revalidates authentication when browser history restores a protected page", async () => {
    const { container, unmount } = await renderComponent(
      <AuthProvider>
        <Probe />
      </AuthProvider>,
    );
    expect(container.textContent).toBe("user-1");
    expect(mocks.me).toHaveBeenCalledTimes(1);

    mocks.me.mockRejectedValueOnce(new Error("session cookie expired"));
    await act(async () => {
      window.dispatchEvent(new PopStateEvent("popstate"));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(mocks.me).toHaveBeenCalledTimes(2);
    expect(mocks.clearAuthenticatedData).toHaveBeenCalledTimes(1);
    expect(container.textContent).toBe("anonymous");
    await unmount();
  });
});
