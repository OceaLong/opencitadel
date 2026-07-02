import { get, post } from "./fetch";
import type { AuthUser } from "./auth";

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

export const adminApi = {
  users: () => get<{ users: AdminUser[] }>("/admin/users"),
  invite: (email: string) => post<{ url: string }>("/admin/invitations", { email }),
  usage: (params?: { user_id?: string; team_id?: string }) => get<Record<string, number>>("/admin/usage", params),
  audit: () => get<{ logs: AuditLog[] }>("/admin/audit"),
};
