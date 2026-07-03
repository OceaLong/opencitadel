import type { AuthUser } from "./auth";
import { del, get, patch, post, put } from "./fetch";
import type { TeamMember, TeamMemberDetail } from "./team";

export type AdminUser = AuthUser & { token_version: number };

export type Quota = {
  monthly_token_limit?: number | null;
  daily_session_limit?: number | null;
  max_concurrent_tasks?: number | null;
  max_storage_bytes?: number | null;
};

export type AuditLog = {
  id: string;
  actor_user_id?: string | null;
  action: string;
  resource_type: string;
  resource_id: string;
  team_id?: string | null;
  request_id: string;
  created_at: string;
};

export type AuditLogDetail = AuditLog & {
  actor_ip?: string;
  metadata?: Record<string, unknown>;
  chain_seq?: number | null;
};

export type UsageSummary = {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cached_tokens: number;
  call_count: number;
};

export type UsageTimeseriesPoint = {
  date: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cached_tokens: number;
  call_count: number;
};

export type UsageBreakdownItem = {
  key: string;
  total_tokens: number;
  call_count: number;
};

export type UsageBreakdownDimension = "model" | "user" | "team" | "agent";

export type AuditSummary = {
  by_day: Array<{ date: string; count: number }>;
  by_action: Array<{ action: string; count: number }>;
};

export type PlatformInvitation = {
  id: string;
  email?: string | null;
  status: "pending" | "accepted" | "expired";
  invited_by?: string | null;
  expires_at: string;
  accepted_at?: string | null;
  accepted_user_id?: string | null;
  created_at: string;
};

export type AdminOverview = {
  total_users: number;
  active_users: number;
  disabled_users: number;
  admin_users: number;
  pending_invitations: number;
  accepted_invitations: number;
  expired_invitations: number;
  total_teams: number;
  total_sessions: number;
};

export type AdminTeam = {
  id: string;
  name: string;
  description: string;
  created_by?: string | null;
  created_at: string;
  member_count: number;
};

export type AdminDateRangeParams = {
  start_at?: string;
  end_at?: string;
  user_id?: string;
  team_id?: string;
};

export type PatchUserPayload = {
  global_role?: "admin" | "user" | "auditor";
  status?: "active" | "disabled";
  display_name?: string;
};

function buildParams(
  params?: Record<string, string | number | undefined>,
): Record<string, string | number | boolean> | undefined {
  if (!params) return undefined;
  const entries = Object.entries(params).filter(([, value]) => value !== undefined && value !== "");
  if (!entries.length) return undefined;
  return Object.fromEntries(entries) as Record<string, string | number | boolean>;
}

export const adminApi = {
  overview: () => get<AdminOverview>("/admin/overview"),

  users: (params?: { limit?: number; offset?: number }) =>
    get<{ users: AdminUser[]; total: number }>("/admin/users", buildParams(params)),

  patchUser: (userId: string, payload: PatchUserPayload) =>
    patch<AdminUser>(`/admin/users/${userId}`, payload),

  deleteUser: (userId: string, strategy: "cascade" | "transfer_to_team" | "anonymize" = "anonymize") =>
    del<{ strategy: string }>(`/admin/users/${userId}?strategy=${strategy}`),

  getQuota: (userId: string) => get<Quota>(`/admin/users/${userId}/quota`),

  putQuota: (userId: string, payload: Quota) => put<Quota>(`/admin/users/${userId}/quota`, payload),

  invite: (email: string) => post<{ url: string }>("/admin/invitations", { email }),

  invitations: (params?: { limit?: number; offset?: number }) =>
    get<{ invitations: PlatformInvitation[]; total: number }>("/admin/invitations", buildParams(params)),

  usageSummary: (params?: AdminDateRangeParams) =>
    get<UsageSummary>("/admin/usage/summary", buildParams(params)),

  usageTimeseries: (params?: AdminDateRangeParams) =>
    get<{ points: UsageTimeseriesPoint[] }>("/admin/usage/timeseries", buildParams(params)),

  usageBreakdown: (
    dimension: UsageBreakdownDimension,
    params?: AdminDateRangeParams & { limit?: number },
  ) =>
    get<{ dimension: UsageBreakdownDimension; items: UsageBreakdownItem[] }>(
      "/admin/usage/breakdown",
      buildParams({ ...params, dimension }),
    ),

  audit: (params?: {
    limit?: number;
    offset?: number;
    action?: string;
    actor_user_id?: string;
    resource_type?: string;
    resource_id?: string;
    start_at?: string;
    end_at?: string;
  }) => get<{ logs: AuditLog[]; total: number }>("/admin/audit", buildParams(params)),

  auditDetail: (logId: string) => get<AuditLogDetail>(`/admin/audit/${logId}`),

  auditSummary: (params?: AdminDateRangeParams) =>
    get<AuditSummary>("/admin/audit/summary", buildParams(params)),

  exportAuditCsvUrl: (params?: {
    action?: string;
    actor_user_id?: string;
    resource_type?: string;
    resource_id?: string;
    start_at?: string;
    end_at?: string;
  }) => {
    const built = buildParams(params ?? {});
    if (!built) return "/api/admin/audit/export";
    const query = new URLSearchParams(
      Object.entries(built).map(([key, value]) => [key, String(value)]),
    ).toString();
    return `/api/admin/audit/export?${query}`;
  },

  teams: (params?: { limit?: number; offset?: number }) =>
    get<{ teams: AdminTeam[]; total: number }>("/admin/teams", buildParams(params)),

  teamMembers: (teamId: string) => get<{ members: TeamMemberDetail[] }>(`/admin/teams/${teamId}/members`),

  deleteTeam: (teamId: string) => del(`/admin/teams/${teamId}`),

  removeTeamMember: (teamId: string, userId: string) =>
    del(`/admin/teams/${teamId}/members/${userId}`),

  updateTeamMemberRole: (teamId: string, userId: string, role: TeamMember["role"]) =>
    patch<TeamMember>(`/admin/teams/${teamId}/members/${userId}`, { role }),
};
