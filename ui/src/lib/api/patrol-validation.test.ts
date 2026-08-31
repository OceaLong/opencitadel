import { describe, expect, it, vi } from "vitest";

import { waitForPackValidation } from "./patrol-validation";
import type { PatrolPack } from "./types";

function pack(status: PatrolPack["status"], ok?: boolean): PatrolPack {
  return {
    id: "pack-1",
    owner_user_id: "user-1",
    name: "Daily",
    slug: "daily",
    status,
    version: 1,
    config: {} as PatrolPack["config"],
    mcp_server_id: "server-1",
    validation_summary: ok === undefined ? {} : { ok },
    created_at: "2026-08-28T00:00:00Z",
    updated_at: "2026-08-28T00:00:00Z",
  };
}

describe("waitForPackValidation", () => {
  it("waits for the kernel-published version-bound result", async () => {
    const load = vi
      .fn<() => Promise<PatrolPack>>()
      .mockResolvedValueOnce(pack("validating"))
      .mockResolvedValueOnce(pack("draft", true));
    const delay = vi.fn<() => Promise<void>>().mockResolvedValue();

    const result = await waitForPackValidation(load, { attempts: 3, delay });

    expect(result.status).toBe("draft");
    expect(result.validation_summary.ok).toBe(true);
    expect(load).toHaveBeenCalledTimes(2);
    expect(delay).toHaveBeenCalledTimes(1);
  });

  it("fails closed when no durable validation result arrives", async () => {
    const load = vi.fn<() => Promise<PatrolPack>>().mockResolvedValue(pack("validating"));
    const delay = vi.fn<() => Promise<void>>().mockResolvedValue();

    await expect(waitForPackValidation(load, { attempts: 2, delay })).rejects.toThrow(
      "Patrol validation did not reach a terminal state",
    );
  });
});
