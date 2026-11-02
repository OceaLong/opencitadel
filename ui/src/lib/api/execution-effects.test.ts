import { afterEach, expect, test, vi } from "vitest";

import { executionEffectsApi } from "./execution-effects";

afterEach(() => vi.unstubAllGlobals());

test("effect request preserves idempotency and explicit scope through transport", async () => {
  const calls: { url: string; init: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url, init) => {
      calls.push({ url: String(url), init });
      return new Response(JSON.stringify({ code: 200, data: { request_id: "req" } }), {
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  await executionEffectsApi.query("run/a", "op/b", 12, "same-key", {
    workspaceId: "workspace-a",
    headers: { "X-Effect-Scope-Reason": "Incident 123" },
  });
  expect(calls[0].url).toContain("/execution-runs/run%2Fa/effects/op%2Fb/queries");
  const headers = new Headers(calls[0].init.headers);
  expect(headers.get("Idempotency-Key")).toBe("same-key");
  expect(headers.get("X-Workspace-Id")).toBe("workspace-a");
  expect(headers.get("X-Effect-Scope-Reason")).toBe("Incident 123");
  expect(JSON.parse(calls[0].init.body as string)).toEqual({ expected_revision: 12 });
});
