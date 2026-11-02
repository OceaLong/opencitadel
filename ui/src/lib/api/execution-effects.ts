import { get, post, type RequestOptions } from "./fetch";

export type EffectEvidenceRef = { ref: string; sha256: string };
export type EffectAttestation = {
  source: string;
  action: string;
  target: string;
  occurred_at: string;
  method: string;
  responsible_actor: string;
};
export type EffectRequest = {
  request_id: string;
  run_id: string;
  operation_id: string;
  status: string;
  expected_revision: number;
  error_code: string | null;
  created_at: string;
  updated_at: string;
};
export type EffectOperation = {
  operation_id: string;
  run_id: string;
  invocation_id: string;
  activity_id: string;
  revision: number;
  state: string;
  capability: string | Record<string, unknown>;
  wait_reason: string | null;
  contract_digest: string;
  arguments_digest: string;
  target_digest: string;
  provider_identity: string | Record<string, unknown>;
  allowed_actions: string[];
  verification_binding: { action?: string; target?: string };
  receipts: Record<string, unknown>[];
  query_history: EffectRequest[];
  accounting_state: string;
  contract_drift: Record<string, unknown> | null;
  last_confirmed_fact: Record<string, unknown> | null;
};
export type EffectResolution = {
  expected_revision: number;
  decision: "applied" | "not_applied";
  evidence_refs: EffectEvidenceRef[];
  reason: string;
  attestation: EffectAttestation;
  result_evidence_ref?: EffectEvidenceRef | null;
};
const path = (run: string, operation?: string) =>
  `/execution-runs/${encodeURIComponent(run)}/effects${operation ? `/${encodeURIComponent(operation)}` : ""}`;
function keyed(key: string, options?: RequestOptions): RequestOptions {
  const headers = new Headers(options?.headers);
  headers.set("Idempotency-Key", key);
  return { ...options, headers: Object.fromEntries(headers.entries()) };
}
export const executionEffectsApi = {
  list: (run: string, options?: RequestOptions): Promise<EffectOperation[]> =>
    get(path(run), undefined, options),
  get: (run: string, operation: string, options?: RequestOptions): Promise<EffectOperation> =>
    get(path(run, operation), undefined, options),
  query: (
    run: string,
    operation: string,
    revision: number,
    key: string,
    options?: RequestOptions,
  ): Promise<EffectRequest> =>
    post(`${path(run, operation)}/queries`, { expected_revision: revision }, keyed(key, options)),
  resolve: (
    run: string,
    operation: string,
    body: EffectResolution,
    key: string,
    options?: RequestOptions,
  ): Promise<EffectRequest> =>
    post(`${path(run, operation)}/resolutions`, body, keyed(key, options)),
  abandon: (
    run: string,
    operation: string,
    revision: number,
    reason: string,
    key: string,
    options?: RequestOptions,
  ): Promise<EffectRequest> =>
    post(
      `${path(run, operation)}/abandon`,
      { expected_revision: revision, reason },
      keyed(key, options),
    ),
  request: (
    run: string,
    operation: string,
    request: string,
    options?: RequestOptions,
  ): Promise<EffectRequest> =>
    get(`${path(run, operation)}/requests/${encodeURIComponent(request)}`, undefined, options),
};
