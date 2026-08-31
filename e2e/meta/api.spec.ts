import { expect, test, type Page } from "@playwright/test";

import { appApi } from "../support/api";

test("uses the authenticated browser transport and preserves negative status", async () => {
  let request:
    | { requestPath: string; requestInit: { method?: string; body?: unknown } }
    | undefined;
  const page = {
    evaluate: async (_callback: unknown, value: typeof request) => {
      request = value;
      return {
        status: 409,
        payload: {
          code: 409,
          msg: "conflict",
          data: { current: 2 },
          error_key: "runtimePolicy.headConflict",
          error_params: { policy: "execution" },
        },
      };
    },
  } as unknown as Page;

  const result = await appApi<{ current: number }>(
    page,
    "/runtime-policies/execution",
    {
      method: "POST",
      body: { expected_head_version: 1 },
      expectStatus: 409,
    },
  );

  expect(request).toEqual({
    requestPath: "/runtime-policies/execution",
    requestInit: { method: "POST", body: { expected_head_version: 1 } },
  });
  expect(result).toEqual({
    status: 409,
    code: 409,
    msg: "conflict",
    data: { current: 2 },
    errorKey: "runtimePolicy.headConflict",
    errorParams: { policy: "execution" },
  });
});

test("mirrors the product workspace and CSRF headers in browser API calls", async () => {
  const previousDocument = globalThis.document;
  const previousWindow = globalThis.window;
  const previousFetch = globalThis.fetch;
  let requestHeaders: HeadersInit | undefined;
  Object.defineProperty(globalThis, "document", {
    configurable: true,
    value: { cookie: "csrf_token=csrf-value" },
  });
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      localStorage: {
        getItem: (key: string) =>
          key === "opencitadel-active-workspace" ? "team-1" : null,
      },
    },
  });
  globalThis.fetch = (async (
    _input: string | URL | Request,
    init?: RequestInit,
  ) => {
    requestHeaders = init?.headers;
    return {
      status: 200,
      json: async () => ({ code: 200, msg: "success", data: null }),
    } as Response;
  }) as typeof fetch;
  const page = {
    evaluate: async <TArgument, TResult>(
      callback: (value: TArgument) => TResult | Promise<TResult>,
      value: TArgument,
    ) => callback(value),
  } as unknown as Page;

  try {
    await appApi(page, "/sessions", {
      method: "POST",
      body: { title: "scope" },
      headers: { "Idempotency-Key": "acceptance-request-1" },
    });
    expect(requestHeaders).toMatchObject({
      "X-CSRF-Token": "csrf-value",
      "X-Workspace-Id": "team-1",
      "Idempotency-Key": "acceptance-request-1",
    });
  } finally {
    Object.defineProperty(globalThis, "document", {
      configurable: true,
      value: previousDocument,
    });
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: previousWindow,
    });
    globalThis.fetch = previousFetch;
  }
});
