import { describe, expect, it, vi } from "vitest";

import type { ClientDataScope } from "./client-data-scope";
import { ScopedResourceCache } from "./scoped-resource-cache";

const SCOPE: ClientDataScope = { userId: "u1", workspaceId: "w1" };

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe("ScopedResourceCache", () => {
  it("coalesces concurrent reads inside one identity scope", async () => {
    const cache = new ScopedResourceCache<string>();
    const result = deferred<string>();
    const loader = vi.fn(() => result.promise);

    const first = cache.load(SCOPE, "skills", loader);
    const second = cache.load(SCOPE, "skills", loader);
    result.resolve("ready");

    await expect(first).resolves.toBe("ready");
    await expect(second).resolves.toBe("ready");
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it("never shares values across users or workspaces", async () => {
    const cache = new ScopedResourceCache<string>();
    const loader = vi.fn(async (scope: ClientDataScope) => `${scope.userId}:${scope.workspaceId}`);

    await expect(cache.load(SCOPE, "skills", loader)).resolves.toBe("u1:w1");
    await expect(cache.load({ userId: "u2", workspaceId: "w1" }, "skills", loader)).resolves.toBe(
      "u2:w1",
    );
    await expect(cache.load({ userId: "u1", workspaceId: "w2" }, "skills", loader)).resolves.toBe(
      "u1:w2",
    );
    expect(loader).toHaveBeenCalledTimes(3);
  });

  it("evicts rejected loads so a retry can succeed", async () => {
    const cache = new ScopedResourceCache<string>();
    const loader = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce("recovered");

    await expect(cache.load(SCOPE, "inference", loader)).rejects.toThrow("offline");
    await expect(cache.load(SCOPE, "inference", loader)).resolves.toBe("recovered");
    expect(loader).toHaveBeenCalledTimes(2);
  });

  it("does not restore a late result after scope invalidation", async () => {
    const cache = new ScopedResourceCache<string>();
    const result = deferred<string>();
    const pending = cache.load(SCOPE, "inference", () => result.promise);

    cache.invalidateScope(SCOPE);
    result.resolve("stale");

    await expect(pending).resolves.toBe("stale");
    expect(cache.peek(SCOPE, "inference")).toBeUndefined();
  });
});
