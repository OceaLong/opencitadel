import type { Page } from "@playwright/test";

export type ApiResult<T> = {
  status: number;
  code: number;
  msg: string;
  data: T;
  errorKey?: string;
  errorParams?: Record<string, string>;
};

type ApiInit = {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
  expectStatus?: number | readonly number[];
};

type ApiEnvelope<T> = {
  code?: unknown;
  msg?: unknown;
  data?: T;
  error_key?: unknown;
  error_params?: unknown;
};

export async function appApi<T>(
  page: Page,
  path: string,
  init: ApiInit = {},
): Promise<ApiResult<T>> {
  if (!path.startsWith("/") || path.startsWith("//")) {
    throw new Error(`API path must be root-relative: ${path}`);
  }
  const method = init.method?.toUpperCase() ?? "GET";
  const browserResponse = await page.evaluate(
    async ({ requestPath, requestInit }) => {
      const csrf = document.cookie
        .split("; ")
        .find((cookie) => cookie.startsWith("csrf_token="))
        ?.split("=")
        .slice(1)
        .join("=");
      const workspaceId = window.localStorage.getItem(
        "opencitadel-active-workspace",
      );
      const response = await fetch(`/api${requestPath}`, {
        method: requestInit.method,
        credentials: "include",
        headers: {
          Accept: "application/json",
          ...(requestInit.body === undefined
            ? {}
            : { "Content-Type": "application/json" }),
          ...(csrf ? { "X-CSRF-Token": decodeURIComponent(csrf) } : {}),
          ...(workspaceId ? { "X-Workspace-Id": workspaceId } : {}),
          ...requestInit.headers,
        },
        body:
          requestInit.body === undefined
            ? undefined
            : JSON.stringify(requestInit.body),
      });
      return {
        status: response.status,
        payload: await response.json(),
      };
    },
    {
      requestPath: path,
      requestInit: {
        method,
        body: init.body,
        ...(init.headers ? { headers: init.headers } : {}),
      },
    },
  );
  const payload = browserResponse.payload as ApiEnvelope<T>;
  const expected = Array.isArray(init.expectStatus)
    ? init.expectStatus
    : [init.expectStatus ?? 200];
  const status = browserResponse.status;
  if (!expected.includes(status)) {
    throw new Error(
      `${method} ${path}: expected HTTP ${expected.join(" or ")}, got ${status}: ${String(payload.msg ?? "invalid API response")}`,
    );
  }
  if (typeof payload.code !== "number" || typeof payload.msg !== "string") {
    throw new Error(`${method} ${path}: malformed API envelope`);
  }
  if (payload.code !== status) {
    throw new Error(
      `${method} ${path}: HTTP status ${status} does not match API code ${payload.code}`,
    );
  }
  return {
    status,
    code: payload.code,
    msg: payload.msg,
    data: payload.data as T,
    ...(typeof payload.error_key === "string"
      ? { errorKey: payload.error_key }
      : {}),
    ...(typeof payload.error_params === "object" &&
    payload.error_params !== null &&
    !Array.isArray(payload.error_params)
      ? { errorParams: payload.error_params as Record<string, string> }
      : {}),
  };
}
