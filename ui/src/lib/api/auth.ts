import { get, post } from "./fetch";

export type AuthUser = {
  id: string;
  email: string;
  username: string;
  display_name: string;
  avatar_url: string;
  global_role: "admin" | "user" | "auditor";
  status: "active" | "disabled";
  created_at: string;
  last_login_at?: string | null;
};

export const authApi = {
  me: () => get<AuthUser>("/auth/me", undefined, { skipAuthRedirect: true }),
  login: (email_or_username: string, password: string) =>
    post<AuthUser>("/auth/login", { email_or_username, password }),
  register: (params: { invite_token: string; email: string; username: string; password: string }) =>
    post<AuthUser>("/auth/register", params),
  logout: () => post("/auth/logout", {}),
};
