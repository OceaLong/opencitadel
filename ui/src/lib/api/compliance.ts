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

  getComplianceReport: (params?: {
    framework?: string;
    start?: string;
    end?: string;
    format?: "json" | "md" | "pdf";
  }) => {
    const qs = new URLSearchParams();
    if (params?.framework) qs.set("framework", params.framework);
    if (params?.start) qs.set("start", params.start);
    if (params?.end) qs.set("end", params.end);
    if (params?.format) qs.set("format", params.format);
    const q = qs.toString();
    if (params?.format === "md" || params?.format === "pdf") {
      return fetch(`/api/admin/compliance/report?${q}`, { credentials: "include" });
    }
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
