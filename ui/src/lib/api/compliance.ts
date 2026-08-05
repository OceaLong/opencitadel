import { get } from "./fetch";

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
  operator_scope?: string | null;
  gate_profile?: string | null;
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
  summary: { pass: number; gap: number; na: number; total: number };
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
  status: string;
  gate_profile?: string | null;
  operator_scope?: string | null;
  created_at: string;
  updated_at: string;
};

export type GovernanceApprovalRow = {
  action: string;
  decision?: string | null;
  actor_user_id?: string | null;
  created_at: string;
  pending_phase?: string | null;
  tool?: string | null;
  approval_batch_id?: string | null;
  feedback?: string | null;
};

export type GovernanceGateHitRow = {
  tool?: string | null;
  gated?: boolean | null;
  gate_profile?: string | null;
  created_at: string;
};

export type GovernanceCheckpointRow = {
  id: string;
  anchor_type: string;
  label?: string | null;
  created_at: string;
};

export type GovernanceProfile = {
  session: GovernanceProfileSession;
  chain: {
    verified: boolean;
    checked_entries?: number | null;
  };
  approvals: GovernanceApprovalRow[];
  gate_hits: GovernanceGateHitRow[];
  checkpoints: GovernanceCheckpointRow[];
  terminal: {
    status: string;
    reached_at: string;
  };
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
    return get<{ sessions: EvidenceSessionItem[] }>(
      `/admin/evidence/sessions${q ? `?${q}` : ""}`,
    );
  },

  evidencePackageUrl: (sessionId: string) =>
    `/api/admin/evidence/sessions/${sessionId}/package`,

  getGovernanceProfile: (sessionId: string) =>
    get<GovernanceProfile>(`/admin/governance/sessions/${sessionId}/profile`),

  getComplianceReport: (params?: {
    framework?: string;
    start?: string;
    end?: string;
  }) => {
    const qs = new URLSearchParams();
    if (params?.framework) qs.set("framework", params.framework);
    if (params?.start) qs.set("start", params.start);
    if (params?.end) qs.set("end", params.end);
    qs.set("format", "json");
    const q = qs.toString();
    return get<{ report: ComplianceReport }>(`/admin/compliance/report?${q}`);
  },

  complianceReportUrl: (params?: {
    framework?: string;
    start?: string;
    end?: string;
    format?: "md" | "pdf";
  }) => {
    const qs = new URLSearchParams();
    if (params?.framework) qs.set("framework", params.framework);
    if (params?.start) qs.set("start", params.start);
    if (params?.end) qs.set("end", params.end);
    qs.set("format", params?.format ?? "pdf");
    return `/api/admin/compliance/report?${qs.toString()}`;
  },
};
