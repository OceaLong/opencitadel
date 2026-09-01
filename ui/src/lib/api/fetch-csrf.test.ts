// @vitest-environment jsdom

import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { authenticatedFetch } from "./fetch";

beforeAll(() => {
  // 该 jsdom 环境未提供 localStorage；buildAuthHeaders 会读取它，补一个桩即可。
  if (!("localStorage" in window) || window.localStorage == null) {
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
    });
  }
});

function mockCookies(value: string) {
  vi.spyOn(document, "cookie", "get").mockReturnValue(value);
}

function csrfHeaderFrom(fetchMock: ReturnType<typeof vi.fn>): string | undefined {
  const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
  const headers = (init?.headers ?? {}) as Record<string, string>;
  return headers["X-CSRF-Token"];
}

describe("CSRF cookie __Host- prefix resolution", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("prefers __Host-csrf_token over the bare csrf_token (production/HTTPS)", async () => {
    mockCookies("__Host-csrf_token=hostvalue; csrf_token=barevalue");
    const fetchMock = vi.fn(async () => new Response(null, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await authenticatedFetch("/x", { method: "POST" });

    expect(csrfHeaderFrom(fetchMock)).toBe("hostvalue");
  });

  it("falls back to bare csrf_token when no __Host- cookie is present (dev/http)", async () => {
    mockCookies("csrf_token=barevalue");
    const fetchMock = vi.fn(async () => new Response(null, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    await authenticatedFetch("/x", { method: "POST" });

    expect(csrfHeaderFrom(fetchMock)).toBe("barevalue");
  });
});
