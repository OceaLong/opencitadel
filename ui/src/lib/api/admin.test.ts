import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  del: vi.fn(() => Promise.resolve({ strategy: "anonymize" })),
}));

vi.mock("./fetch", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./fetch")>();
  return { ...actual, del: mocks.del };
});

import { adminApi } from "./admin";

describe("adminApi.deleteUser", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("defaults to the anonymize strategy without a team_id", async () => {
    await adminApi.deleteUser("u1");
    expect(mocks.del).toHaveBeenCalledWith("/admin/users/u1?strategy=anonymize");
  });

  it("appends team_id when transferring to a team so the backend does not 400", async () => {
    await adminApi.deleteUser("u1", "transfer_to_team", "team-9");
    expect(mocks.del).toHaveBeenCalledWith(
      "/admin/users/u1?strategy=transfer_to_team&team_id=team-9",
    );
  });

  it("omits team_id for non-transfer strategies even if one is passed", async () => {
    await adminApi.deleteUser("u1", "cascade");
    expect(mocks.del).toHaveBeenCalledWith("/admin/users/u1?strategy=cascade");
  });
});
