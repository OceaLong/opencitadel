import { translate } from "@/i18n/translate";

import { authenticatedFetch, get } from "./fetch";

/** 导出类接口的共用兜底：非 2xx 时抛可展示的错误（供调用方 toast）。 */
async function fetchBlob(path: string): Promise<Blob> {
  const response = await authenticatedFetch(path);
  if (!response.ok) {
    throw new Error(translate("errors.downloadFailedWithStatus", { status: response.status }));
  }
  return response.blob();
}

export type ChainVerifyResult = {
  ok: boolean;
  total: number;
  first_broken_seq?: number | null;
  checked_at: string;
  session_id?: string | null;
  session_entries?: number | null;
  session_ok?: boolean | null;
  session_first_broken_seq?: number | null;
};

export type EvidenceSessionItem = {
  session_id: string;
  title: string;
  owner_user_id: string | null;
  team_id: string | null;
  operator_scope?: string | null;
  status: string;
  updated_at?: string | null;
  chain_ok: boolean;
  tool_invocation_count: number;
  governance_action_count: number;
};

export type ComplianceReport = {
  generated_at: string;
  start_at?: string | null;
  end_at?: string | null;
  frameworks: string[];
  chain_verification: ChainVerifyResult;
  summary: {
    pass: number;
    gap: number;
    attention: number;
    not_verified: number;
    na: number;
    total: number;
  };
  controls: Array<{
    framework: string;
    control_id: string;
    title: string;
    requirement: string;
    capability: string;
    evaluator: string;
    status: string;
    evidence: string[];
  }>;
};

export type GovernanceProfileSession = {
  id: string;
  title: string;
  owner_user_id: string | null;
  team_id: string | null;
  status: string;
  operator_scope?: string | null;
  operator_domains: string[];
  created_at: string;
  updated_at: string;
};

export type GovernanceApprovalRow = {
  approval_id: string;
  run_id: string;
  approval_kind: string;
  subject_activity_id: string;
  subject_label: string;
  risk_summary: string;
  status: "pending" | "approved" | "rejected" | "cancelled";
  decision?: string | null;
  decided_by_user_id?: string | null;
  feedback: string;
  requested_at: string;
  decided_at?: string | null;
};

export type GovernanceRunRow = {
  run_id: string;
  family: string;
  status: string;
  created_at: string;
  updated_at: string;
  terminal_at?: string | null;
};

export type GovernanceActivityRow = {
  activity_id: string;
  run_id: string;
  activity_type: string;
  status: string;
  attempt: number;
  failure_code?: string | null;
  created_at: string;
  terminal_at?: string | null;
};

export type GovernanceProfile = {
  session: GovernanceProfileSession;
  chain: {
    verified: boolean;
    checked_runs: number;
    checked_entries: number;
  };
  runs: GovernanceRunRow[];
  approvals: GovernanceApprovalRow[];
  activities: GovernanceActivityRow[];
};

export type ApprovalStats = {
  pending_count: number;
  avg_decision_seconds?: number | null;
  outcomes: {
    approved: number;
    rejected: number;
    cancelled: number;
  };
};

export type GovernanceDailyCount = {
  date: string;
  approval_requests: number;
  activity_failures: number;
};

export type GovernanceDailyPatrolStat = {
  date: string;
  runs: number;
  findings: number;
};

export type RemediationStats = {
  by_status: {
    proposed: number;
    executing: number;
    executed: number;
    verified: number;
    failed: number;
    cancelled: number;
  };
  success_rate?: number | null;
};

export type GovernanceOverview = {
  approvals: ApprovalStats;
  interceptions: GovernanceDailyCount[];
  patrol: GovernanceDailyPatrolStat[];
  remediation: RemediationStats;
  chain: ChainVerifyResult;
};

export const complianceApi = {
  verifyChain: () => get<ChainVerifyResult>("/admin/audit/verify-chain"),

  verifySessionChain: (sessionId: string) =>
    get<ChainVerifyResult>(`/admin/audit/verify-chain/sessions/${sessionId}`),

  listEvidenceSessions: (params?: { limit?: number; offset?: number }) => {
    const qs = new URLSearchParams();
    if (params?.limit != null) qs.set("limit", String(params.limit));
    if (params?.offset != null) qs.set("offset", String(params.offset));
    const q = qs.toString();
    return get<{ sessions: EvidenceSessionItem[] }>(`/admin/evidence/sessions${q ? `?${q}` : ""}`);
  },

  /** 下载会话证据包（ZIP）。走 authenticatedFetch，403/500 抛错供 toast。 */
  downloadEvidencePackage: (sessionId: string): Promise<Blob> =>
    fetchBlob(`/admin/evidence/sessions/${sessionId}/package`),

  getGovernanceProfile: (sessionId: string) =>
    get<GovernanceProfile>(`/admin/governance/sessions/${sessionId}/profile`),

  getGovernanceOverview: (params?: { days?: number }) => {
    const qs = new URLSearchParams();
    if (params?.days != null) qs.set("days", String(params.days));
    const q = qs.toString();
    return get<GovernanceOverview>(`/admin/governance/overview${q ? `?${q}` : ""}`);
  },

  getComplianceReport: (params?: { framework?: string; start?: string; end?: string }) => {
    const qs = new URLSearchParams();
    if (params?.framework) qs.set("framework", params.framework);
    if (params?.start) qs.set("start", params.start);
    if (params?.end) qs.set("end", params.end);
    qs.set("format", "json");
    const q = qs.toString();
    return get<{ report: ComplianceReport }>(`/admin/compliance/report?${q}`);
  },

  /** 导出合规报告（md/pdf）。走 authenticatedFetch，403/500 抛错供 toast。 */
  downloadComplianceReport: (params?: {
    framework?: string;
    start?: string;
    end?: string;
    format?: "md" | "pdf";
  }): Promise<Blob> => {
    const qs = new URLSearchParams();
    if (params?.framework) qs.set("framework", params.framework);
    if (params?.start) qs.set("start", params.start);
    if (params?.end) qs.set("end", params.end);
    qs.set("format", params?.format ?? "pdf");
    return fetchBlob(`/admin/compliance/report?${qs.toString()}`);
  },
};
