import { get, post } from "./fetch";

export type ExecutionRecoveryStatus = {
  scope_lags: { owner_scope_key: string; lag: number }[];
  poisoned_scopes: { owner_scope_key: string; reason: string; last_error: string }[];
  poisoned_runs: {
    run_id: string;
    owner_scope_key: string;
    reason: string;
    last_error: string;
    failure_count: number;
    next_attempt_at: string | null;
  }[];
  recovery_requests: {
    id: string;
    owner_scope_key: string;
    status: string;
    reason: string;
    result: Record<string, unknown>;
  }[];
};
export const executionRecoveryApi = {
  status: () => get<ExecutionRecoveryStatus>("/admin/execution/projection-status"),
  request: (scope_key: string, reason: string) =>
    post<{ id: string; status: string }>("/admin/execution/recover", { scope_key, reason }),
};
