type PatrolPackStatus = "draft" | "validating" | "active" | "paused" | "invalid";
type PatrolRunStatus =
  | "queued"
  | "running"
  | "completed"
  | "completed_with_findings"
  | "failed"
  | "cancelled";
type PatrolCheckStatus = "pass" | "warn" | "fail" | "error" | "skipped";
type PatrolFindingStatus = "open" | "acknowledged" | "resolved" | "false_positive";

export type PatrolPackConfig = {
  schema_version: 1;
  target_ref: string;
  timezone: string;
  schedule: { cron: string; enabled: boolean };
  scope: { cluster: string; namespaces: string[]; environment: "dev" | "staging" | "production" };
  defaults: {
    timeout_seconds: number;
    run_timeout_seconds: number;
    max_evidence_items: number;
  };
  checks: Array<{
    id: string;
    title: string;
    enabled: boolean;
    probe: { tool: string; args: Record<string, unknown>; output_schema_hash: string };
    assertions: Array<Record<string, unknown>>;
    severity_on_fail: "warning" | "critical";
    required_evidence: string[];
    runbook_ref?: string | null;
  }>;
};

export type PatrolPack = {
  id: string;
  owner_user_id: string;
  team_id?: string | null;
  name: string;
  slug: string;
  status: PatrolPackStatus;
  version: number;
  config: PatrolPackConfig;
  mcp_server_id: string;
  skill_id: string;
  scheduled_job_id?: string | null;
  last_validated_at?: string | null;
  last_validated_version?: number | null;
  validation_summary: {
    ok?: boolean;
    errors?: string[];
    enabled_tools?: string[];
    capability_hash?: string;
    dry_run?: unknown;
  };
  next_run_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type PatrolRun = {
  id: string;
  pack_id: string;
  pack_version: number;
  session_id?: string | null;
  status: PatrolRunStatus;
  trigger_type: "manual" | "schedule" | "replay";
  started_at?: string | null;
  finished_at?: string | null;
  first_reviewed_at?: string | null;
  duration_ms?: number | null;
  counts: Record<PatrolCheckStatus, number>;
  evidence_completeness?: number | null;
  summary: Record<string, unknown>;
  report_artifact_id?: string | null;
  created_at: string;
};

type PatrolCheckResult = {
  id: string;
  run_id: string;
  check_id: string;
  status: PatrolCheckStatus;
  severity: "info" | "warning" | "critical";
  observed: Record<string, unknown>;
  assertion_results: Array<Record<string, unknown>>;
  evidence_refs: Array<Record<string, unknown>>;
  explanation: string;
  error_code?: string | null;
  error_message?: string | null;
  fingerprint: string;
  started_at: string;
  finished_at: string;
};

export type PatrolFinding = {
  id: string;
  run_id: string;
  check_result_id: string;
  fingerprint: string;
  severity: "info" | "warning" | "critical";
  status: PatrolFindingStatus;
  title: string;
  summary: string;
  first_seen_at: string;
  last_seen_at: string;
  occurrence_count: number;
  decided_by?: string | null;
  decided_at?: string | null;
  decision_reason?: string | null;
};

export type PatrolRunDetail = PatrolRun & {
  check_results: PatrolCheckResult[];
  findings: PatrolFinding[];
};
export type PatrolPackMetrics = {
  window_days: number;
  sample_size: number;
  scheduled_run_count: number;
  scheduled_success_rate?: number | null;
  finding_count: number;
  false_positive_count: number;
  median_review_minutes?: number | null;
};
export type PatrolPackList = { items: PatrolPack[]; limit: number; offset: number };
export type PatrolRunList = { items: PatrolRun[]; limit: number; offset: number };
