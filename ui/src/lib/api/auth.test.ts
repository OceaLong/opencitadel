import { describe, expect, it, vi } from "vitest";

vi.mock("./fetch", () => ({ get: vi.fn(), post: vi.fn() }));

import { authApi } from "./auth";
import { get } from "./fetch";

describe("authApi", () => {
  it("always revalidates the current identity instead of accepting browser cache", async () => {
    vi.mocked(get).mockResolvedValue({ id: "user-1" } as never);

    await authApi.me();

    expect(get).toHaveBeenCalledWith("/auth/me", undefined, {
      cache: "no-store",
      skipAuthRedirect: true,
    });
  });
});
