import { get, type RequestOptions } from "./fetch";
import type { components } from "./generated/schema";

export type CapabilitySnapshot = components["schemas"]["CapabilityResponse"];
export type CapabilityState = components["schemas"]["CapabilityStateResponse"];
export type CapabilityStateValue = components["schemas"]["CapabilityStateValue"];

export const CAPABILITY_NAMES = [
  "chat",
  "embeddings",
  "rerank",
  "a2a",
  "ops_patrol",
  "ops_patrol_remediation",
  "report_pdf",
] as const;

export type CapabilityName = (typeof CAPABILITY_NAMES)[number];

export function isCapabilityAvailable(state: CapabilityState | undefined): boolean {
  return state?.state === "available";
}

export function canProposePatrolRemediation(state: CapabilityState | undefined): boolean {
  return (
    state?.state === "available" ||
    (state?.state === "degraded" && state.details?.mode === "propose_only")
  );
}

export function needsInferenceConfiguration(state: CapabilityState | undefined): boolean {
  return (
    state?.state === "not_configured" || state?.state === "degraded" || state?.state === "denied"
  );
}

export const capabilitiesApi = {
  get: (options?: RequestOptions): Promise<CapabilitySnapshot> =>
    get("/capabilities", undefined, options),
};

// These grants are authorization, independently of runtime capability state.
export type ExecutionGrant = NonNullable<CapabilitySnapshot["grants"]>[number];
export function hasExecutionGrant(
  snapshot: CapabilitySnapshot | undefined,
  grant: ExecutionGrant,
): boolean {
  return snapshot?.grants?.includes(grant) ?? false;
}
