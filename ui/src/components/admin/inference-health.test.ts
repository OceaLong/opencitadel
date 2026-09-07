import { describe, expect, it } from "vitest";

import { inferenceHealth } from "./inference-health";

describe("inferenceHealth", () => {
  it("requires an available chat binding even when the status endpoint responds", () => {
    for (const state of ["not_configured", "disabled", "denied", "degraded"]) {
      expect(
        inferenceHealth({ capabilities: { items: { chat: { state } } }, circuit_breakers: [] })
          .healthy,
      ).toBe(false);
    }
    expect(inferenceHealth({ capabilities: {}, circuit_breakers: [] }).healthy).toBe(false);
  });
  it("reads capability items and reports breaker state", () => {
    const status = {
      capabilities: { generated_at: "today", items: { chat: { state: "available" } } },
      circuit_breakers: [{ model_id: "model", state: "open" }],
    };
    expect(inferenceHealth(status)).toEqual({
      healthy: false,
      capabilities: ["chat: available"],
      breakers: ["model: open"],
    });
    expect(inferenceHealth({ ...status, circuit_breakers: [{ state: "closed" }] }).healthy).toBe(
      true,
    );
  });
});
