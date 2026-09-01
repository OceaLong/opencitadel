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
  me: () =>
    get<AuthUser>("/auth/me", undefined, {
      cache: "no-store",
      skipAuthRedirect: true,
    }),
  login: (email_or_username: string, password: string) =>
    post<AuthUser>("/auth/login", { email_or_username, password }),
  register: (params: { invite_token: string; email: string; username: string; password: string }) =>
    post<AuthUser>("/auth/register", params),
  logout: () => post("/auth/logout", {}),
  /**
   * 已启用的 OAuth 提供商列表。走统一 fetch 层（带 workspace/CSRF header），
   * 并跳过鉴权刷新/跳转——该接口可能在未登录的登录页被调用，失败时静默即可。
   */
  oauthProviders: () =>
    get<string[]>("/auth/oauth/providers", undefined, {
      skipAuthRefresh: true,
      skipAuthRedirect: true,
    }),
};
