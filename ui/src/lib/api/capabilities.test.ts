import { describe, expect, it, vi } from "vitest";

vi.mock("./fetch", () => ({ get: vi.fn() }));

import {
  canProposePatrolRemediation,
  capabilitiesApi,
  type CapabilityState,
  isCapabilityAvailable,
  needsInferenceConfiguration,
} from "./capabilities";
import { get } from "./fetch";

describe("capabilitiesApi", () => {
  it("loads the authenticated capability projection", async () => {
    vi.mocked(get).mockResolvedValue({ items: {} } as never);

    await capabilitiesApi.get();

    expect(get).toHaveBeenCalledWith("/capabilities");
  });

  it("treats only available capabilities as admitted", () => {
    expect(isCapabilityAvailable({ state: "available" } as CapabilityState)).toBe(true);
    expect(isCapabilityAvailable({ state: "degraded" } as CapabilityState)).toBe(false);
    expect(isCapabilityAvailable(undefined)).toBe(false);
  });

  it("allows remediation proposals in available and propose-only modes", () => {
    expect(
      canProposePatrolRemediation({
        state: "available",
        details: { mode: "enabled" },
      } as CapabilityState),
    ).toBe(true);
    expect(
      canProposePatrolRemediation({
        state: "degraded",
        details: { mode: "propose_only" },
      } as CapabilityState),
    ).toBe(true);
    expect(
      canProposePatrolRemediation({
        state: "disabled",
        details: { mode: "disabled" },
      } as CapabilityState),
    ).toBe(false);
  });

  it("distinguishes missing inference configuration from an intentionally disabled consumer", () => {
    expect(needsInferenceConfiguration({ state: "not_configured" } as CapabilityState)).toBe(true);
    expect(needsInferenceConfiguration({ state: "degraded" } as CapabilityState)).toBe(true);
    expect(needsInferenceConfiguration({ state: "denied" } as CapabilityState)).toBe(true);
    expect(needsInferenceConfiguration({ state: "disabled" } as CapabilityState)).toBe(false);
    expect(needsInferenceConfiguration({ state: "available" } as CapabilityState)).toBe(false);
  });
});
