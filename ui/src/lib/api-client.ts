export const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "/api";
export const WORKSPACE_KEY = "opencitadel-workspace";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly payload: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function cookie(name: string): string {
  if (typeof document === "undefined") return "";
  const prefix = `${encodeURIComponent(name)}=`;
  const value = document.cookie.split("; ").find((item) => item.startsWith(prefix));
  return value ? decodeURIComponent(value.slice(prefix.length)) : "";
}

function headers(method: string, body: BodyInit | null | undefined): Headers {
  const value = new Headers({ Accept: "application/json" });
  if (body && !(body instanceof FormData)) value.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = cookie("__Host-csrf_token") || cookie("csrf_token");
    if (csrf) value.set("X-CSRF-Token", csrf);
  }
  if (typeof window !== "undefined") {
    const workspace = window.localStorage.getItem(WORKSPACE_KEY);
    if (workspace) value.set("X-Workspace-Id", workspace);
  }
  return value;
}

async function decode<T>(response: Response): Promise<T> {
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const data = payload as Record<string, unknown> | null;
    const detail = data?.detail as Record<string, unknown> | string | undefined;
    const message =
      (typeof detail === "string" ? detail : String(detail?.key || "")) ||
      String(data?.msg || data?.message || response.statusText || "Request failed");
    throw new ApiError(message, response.status, payload);
  }
  if (payload && typeof payload === "object" && "data" in payload) {
    return (payload as { data: T }).data;
  }
  return payload as T;
}

async function send<T>(path: string, init: RequestInit, retry: boolean): Promise<T> {
  const method = (init.method || "GET").toUpperCase();
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: headers(method, init.body),
    credentials: "include",
    cache: init.cache || "no-store",
  });
  if (response.status === 401 && retry && !path.startsWith("/auth/")) {
    const refreshed = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      headers: headers("POST", undefined),
      credentials: "include",
    });
    if (refreshed.ok) return send<T>(path, init, false);
  }
  return decode<T>(response);
}

export async function api<T>(
  path: string,
  init: RequestInit & { json?: unknown } = {},
): Promise<T> {
  const { json, ...request } = init;
  return send<T>(
    path,
    {
      ...request,
      body: json === undefined ? request.body : JSON.stringify(json),
    },
    true,
  );
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "请求失败";
}
