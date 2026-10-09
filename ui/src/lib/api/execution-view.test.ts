import { afterEach, expect, it, vi } from "vitest";

import { ACTIVE_WORKSPACE_KEY } from "@/lib/storage-keys";

import { authenticatedFetch, get } from "./fetch";

afterEach(() => vi.unstubAllGlobals());

it("preserves caller AbortError", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (_url, options) =>
        new Promise((_resolve, reject) => {
          options.signal.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        }),
    ),
  );
  const controller = new AbortController();
  const promise = get("/execution-runs", undefined, { signal: controller.signal });
  controller.abort();
  await expect(promise).rejects.toHaveProperty("name", "AbortError");
});

it.each(["json", "authenticated"])("keeps original workspace across %s 401 retry", async (kind) => {
  let workspace = "team-a";
  vi.stubGlobal("window", {
    localStorage: { getItem: (key: string) => (key === ACTIVE_WORKSPACE_KEY ? workspace : null) },
  });
  const captured: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url, options) => {
      if (String(url).includes("/auth/refresh")) {
        workspace = "team-b";
        return new Response(JSON.stringify({ code: 200, data: {} }), {
          headers: { "Content-Type": "application/json" },
        });
      }
      captured.push(new Headers(options.headers).get("X-Workspace-Id") || "");
      return new Response(JSON.stringify({ code: 200, data: [] }), {
        status: captured.length === 1 ? 401 : 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  await (kind === "json" ? get("/execution-runs") : authenticatedFetch("/execution-runs"));
  expect(captured).toEqual(["team-a", "team-a"]);
});

it("keeps opaque content selection and collects complete continuation", async () => {
  const { executionViewApi } = await import("./execution-view");
  const urls: URL[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url) => {
      urls.push(new URL(String(url), "https://test.local"));
      const more = urls.length === 1;
      return new Response(
        JSON.stringify({
          code: 200,
          data: {
            availability: "available",
            content: more ? "第一" : "第二",
            truncated: more,
            next_cursor: more ? "opaque/+==" : null,
          },
        }),
        { headers: { "Content-Type": "application/json" } },
      );
    }),
  );
  const blob = await executionViewApi.downloadArtifact("art", {
    version: 7,
    run_id: "run",
    step_id: "attempt",
    at: "cut/+==",
  });
  expect(await blob.text()).toBe("第一第二");
  expect(urls[1].searchParams.get("cursor")).toBe("opaque/+==");
  expect(urls[1].searchParams.get("at")).toBe("cut/+==");
  expect(urls[1].searchParams.get("version")).toBe("7");
});

it("passes Last-Event-ID unchanged and exposes persistent event identity", async () => {
  const { executionViewApi } = await import("./execution-view");
  const received: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url, options) => {
      expect(new Headers(options.headers).get("Last-Event-ID")).toBe("opaque/+==");
      return new Response(
        'event: execution\nid: next-opaque\ndata: {"cursor":"next-opaque","event_id":"fact-id"}\n\n',
      );
    }),
  );
  await executionViewApi.streamEvents("run", (event) => received.push(event), {
    lastEventId: "opaque/+==",
  });
  expect(received).toEqual([{ cursor: "next-opaque", event_id: "fact-id" }]);
});

it("rejects a full download when authority is lost on continuation", async () => {
  const { executionViewApi } = await import("./execution-view");
  let reads = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      reads += 1;
      return new Response(
        JSON.stringify(
          reads === 1
            ? {
                code: 200,
                data: {
                  availability: "available",
                  content: "prefix",
                  truncated: true,
                  next_cursor: "next",
                },
              }
            : { code: 403, msg: "denied", data: { code: "permission_denied" } },
        ),
        { status: reads === 1 ? 200 : 403, headers: { "Content-Type": "application/json" } },
      );
    }),
  );
  await expect(
    executionViewApi.downloadContent("run", "attempt", { at: "exact" }),
  ).rejects.toMatchObject({ code: 403, data: { code: "permission_denied" } });
});

it("freezes workspace across a full download and preserves binary bytes", async () => {
  const { executionViewApi } = await import("./execution-view");
  let workspace = "team-a";
  vi.stubGlobal("window", { localStorage: { getItem: () => workspace } });
  const scopes: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url, options) => {
      if (String(url).endsWith("/download")) return new Response(new Uint8Array([0, 255, 254]));
      scopes.push(new Headers(options.headers).get("X-Workspace-Id") || "");
      workspace = "team-b";
      return new Response(
        JSON.stringify({
          code: 200,
          data: {
            availability: "available",
            content: "text",
            truncated: scopes.length === 1,
            next_cursor: scopes.length === 1 ? "next" : null,
          },
        }),
        { headers: { "Content-Type": "application/json" } },
      );
    }),
  );
  await executionViewApi.downloadSource("citation");
  expect(scopes).toEqual(["team-a", "team-a"]);
  const binary = await executionViewApi.downloadFileSource("citation");
  expect([...new Uint8Array(await binary.arrayBuffer())]).toEqual([0, 255, 254]);
});

it("keeps caller cancellation through pending JSON body and cleans its listener", async () => {
  const caller = new AbortController();
  const add = vi.spyOn(caller.signal, "addEventListener");
  const remove = vi.spyOn(caller.signal, "removeEventListener");
  let inner: AbortSignal | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url, options) => {
      inner = options.signal;
      return new Response(
        new ReadableStream({
          start(controller) {
            inner!.addEventListener(
              "abort",
              () => controller.error(new DOMException("Aborted", "AbortError")),
              { once: true },
            );
          },
        }),
        { headers: { "Content-Type": "application/json" } },
      );
    }),
  );
  const pending = get("/execution-runs", undefined, { signal: caller.signal });
  await vi.waitFor(() => expect(inner).toBeDefined());
  caller.abort();
  expect(inner!.aborted).toBe(true);
  await expect(pending).rejects.toHaveProperty("name", "AbortError");
  expect(remove).toHaveBeenCalledWith("abort", add.mock.calls[0][1]);
});

it("propagates connected execution stream abort while legacy parser consumes it", async () => {
  const { executionViewApi } = await import("./execution-view");
  const { parseSSEStream } = await import("./fetch");
  const caller = new AbortController();
  let connected = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async (_url, options) =>
        new Response(
          new ReadableStream({
            start(controller) {
              options.signal.addEventListener(
                "abort",
                () => controller.error(new DOMException("Aborted", "AbortError")),
                { once: true },
              );
              connected = true;
            },
          }),
        ),
    ),
  );
  const pending = executionViewApi.streamEvents("run", vi.fn(), { signal: caller.signal });
  await vi.waitFor(() => expect(connected).toBe(true));
  caller.abort();
  await expect(pending).rejects.toHaveProperty("name", "AbortError");
  await expect(
    parseSSEStream(
      new ReadableStream({
        start(c) {
          c.error(new DOMException("Aborted", "AbortError"));
        },
      }),
      vi.fn(),
    ),
  ).resolves.toBeUndefined();
});

it("delivers server CRLF frames across every byte boundary", async () => {
  const { executionViewApi } = await import("./execution-view");
  const frame = new TextEncoder().encode(
    'event: execution\r\nid: opaque\r\ndata: {"cursor":"opaque","title":"你好"}\r\n\r\nevent: refresh\r\ndata: {"code":"permission_denied"}\r\n\r\n',
  );
  for (let split = 1; split < frame.length; split++) {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            new ReadableStream({
              start(c) {
                c.enqueue(frame.slice(0, split));
                c.enqueue(frame.slice(split));
                c.close();
              },
            }),
          ),
      ),
    );
    const event = vi.fn(),
      refresh = vi.fn();
    await executionViewApi.streamEvents("run", event, { onRefresh: refresh });
    expect(event, `split ${split}`).toHaveBeenCalledExactlyOnceWith({
      cursor: "opaque",
      title: "你好",
    });
    expect(refresh, `split ${split}`).toHaveBeenCalledWith({ code: "permission_denied" });
  }
});

it.each(["success", "invalid-json", "transport-error"])(
  "cleans request timeout and caller listener after %s",
  async (outcome) => {
    vi.useFakeTimers();
    try {
      const caller = new AbortController();
      const add = vi.spyOn(caller.signal, "addEventListener");
      const remove = vi.spyOn(caller.signal, "removeEventListener");
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => {
          if (outcome === "transport-error") throw new TypeError("Failed to fetch");
          return new Response(outcome === "success" ? '{"code":200,"data":42}' : "{", {
            headers: { "Content-Type": "application/json" },
          });
        }),
      );
      const pending = get("/execution-runs", undefined, { signal: caller.signal });
      if (outcome === "success") await expect(pending).resolves.toBe(42);
      else await expect(pending).rejects.toBeInstanceOf(Error);
      expect(remove).toHaveBeenCalledWith("abort", add.mock.calls[0][1]);
      expect(vi.getTimerCount()).toBe(0);
    } finally {
      vi.useRealTimers();
    }
  },
);

it("times out pending body consumption and clears resources", async () => {
  vi.useFakeTimers();
  try {
    const caller = new AbortController();
    const remove = vi.spyOn(caller.signal, "removeEventListener");
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async (_url, options) =>
          new Response(
            new ReadableStream({
              start(c) {
                options.signal.addEventListener(
                  "abort",
                  () => c.error(new DOMException("Aborted", "AbortError")),
                  { once: true },
                );
              },
            }),
            { headers: { "Content-Type": "application/json" } },
          ),
      ),
    );
    const pending = get("/execution-runs", undefined, { timeout: 25, signal: caller.signal });
    const assertion = expect(pending).rejects.toHaveProperty("code", 408);
    await vi.advanceTimersByTimeAsync(25);
    await assertion;
    expect(remove).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  } finally {
    vi.useRealTimers();
  }
});
it("transports the paired source identity and opaque cohort cursor through the shared fetch client", async () => {
  const { executionViewApi } = await import("./execution-view");
  let request: URL | undefined;
  let workspace: string | null = null;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url, options) => {
      request = new URL(String(url), "https://test.local");
      workspace = new Headers(options.headers).get("X-Workspace-Id");
      return new Response(
        JSON.stringify({
          code: 200,
          data: { items: [], next_cursor: null, completeness: { state: "complete" } },
        }),
        { headers: { "Content-Type": "application/json" } },
      );
    }),
  );
  await executionViewApi.listRuns(
    {
      source_entity_type: "session",
      source_entity_id: "session/+one",
      cursor: "cohort/+==",
      limit: 50,
    },
    { workspaceId: "team-a" },
  );
  expect(request?.searchParams.get("source_entity_type")).toBe("session");
  expect(request?.searchParams.get("source_entity_id")).toBe("session/+one");
  expect(request?.searchParams.get("cursor")).toBe("cohort/+==");
  expect(workspace).toBe("team-a");
});
