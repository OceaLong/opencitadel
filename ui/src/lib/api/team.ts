import { get, post } from "./fetch";

export type Team = { id: string; name: string; description: string; created_by?: string | null; created_at: string };
export type TeamMember = { team_id: string; user_id: string; role: "owner" | "admin" | "member"; joined_at: string };

export const teamApi = {
  list: () => get<{ teams: Team[] }>("/teams"),
  create: (name: string, description = "") => post<Team>("/teams", { name, description }),
  members: (teamId: string) => get<{ members: TeamMember[] }>(`/teams/${teamId}/members`),
  invite: (teamId: string, role: TeamMember["role"] = "member") =>
    post<{ url: string }>(`/teams/${teamId}/invitations`, { role }),
  accept: (token: string) => post<TeamMember>(`/invitations/${token}/accept`, {}),
};
