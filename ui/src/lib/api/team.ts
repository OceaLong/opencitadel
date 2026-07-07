import { del, get, patch, post } from "./fetch";

export type Team = { id: string; name: string; description: string; created_by?: string | null; created_at: string };
export type TeamMember = { team_id: string; user_id: string; role: "owner" | "admin" | "member"; joined_at: string };
export type TeamInvitationPreview = {
  team_id: string;
  team_name: string;
  role: TeamMember["role"];
  status: "pending" | "accepted" | "expired";
  expires_at: string;
  requires_registration: boolean;
  email_hint?: string | null;
};
export type TeamMemberDetail = {
  user_id: string;
  role: TeamMember["role"];
  joined_at: string;
  display_name: string;
  email: string;
  avatar_url: string;
};

export function memberDisplayName(member: Pick<TeamMemberDetail, "display_name" | "email" | "user_id">): string {
  return member.display_name || member.email || member.user_id;
}

export const teamApi = {
  list: () => get<{ teams: Team[] }>("/teams"),
  get: (teamId: string) => get<Team>(`/teams/${teamId}`),
  create: (name: string, description = "") => post<Team>("/teams", { name, description }),
  members: (teamId: string) => get<{ members: TeamMemberDetail[] }>(`/teams/${teamId}/members`),
  invite: (teamId: string, role: TeamMember["role"] = "member", email?: string) =>
    post<{ url: string }>(`/teams/${teamId}/invitations`, { role, email: email?.trim() || null }),
  preview: (token: string) =>
    get<TeamInvitationPreview>(`/invitations/${token}`, undefined, { skipAuthRedirect: true }),
  registerAndAccept: (
    token: string,
    payload: { email: string; username: string; password: string },
  ) => post<TeamMember>(`/invitations/${token}/register`, payload, { skipAuthRedirect: true }),
  accept: (token: string) => post<TeamMember>(`/invitations/${token}/accept`, {}),
  leave: (teamId: string) => post<null>(`/teams/${teamId}/leave`, {}),
  remove: (teamId: string) => del(`/teams/${teamId}`),
  removeMember: (teamId: string, userId: string) => del(`/teams/${teamId}/members/${userId}`),
  updateMemberRole: (teamId: string, userId: string, role: TeamMember["role"]) =>
    patch<TeamMember>(`/teams/${teamId}/members/${userId}`, { role }),
};
