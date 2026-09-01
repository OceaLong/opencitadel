// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

import type { RequestOptions } from "./fetch";

const mocks = vi.hoisted(() => ({
  createSSEStream: vi.fn(),
  get: vi.fn(() => Promise.resolve({ sessions: [] })),
  post: vi.fn(() => Promise.resolve()),
}));

vi.mock("./fetch", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./fetch")>();
  return {
    ...actual,
    createSSEStream: mocks.createSSEStream,
    get: mocks.get,
    post: mocks.post,
  };
});

import { sessionApi } from "./session";

describe("sessionApi search + recycle bin", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("passes the trimmed q keyword to the list endpoint", async () => {
    await sessionApi.getSessions("  hello ");
    expect(mocks.get).toHaveBeenCalledWith("/sessions", { q: "hello" });
  });

  it("omits q entirely when the keyword is empty (behavior unchanged)", async () => {
    await sessionApi.getSessions("");
    expect(mocks.get).toHaveBeenCalledWith("/sessions", undefined);
    await sessionApi.getSessions();
    expect(mocks.get).toHaveBeenCalledWith("/sessions", undefined);
  });

  it("appends q to the stream URL as a query param (not the body)", async () => {
    mocks.createSSEStream.mockResolvedValue(new ReadableStream());
    sessionApi.streamSessions(
      () => {},
      () => {},
      "web ops",
    );
    await vi.waitFor(() => {
      expect(mocks.createSSEStream).toHaveBeenCalledWith(
        "/sessions/stream?q=web%20ops",
        {},
        expect.anything(),
      );
    });
  });

  it("keeps the bare stream URL when no keyword is given", async () => {
    mocks.createSSEStream.mockResolvedValue(new ReadableStream());
    sessionApi.streamSessions(() => {}, () => {});
    await vi.waitFor(() => {
      expect(mocks.createSSEStream).toHaveBeenCalledWith("/sessions/stream", {}, expect.anything());
    });
  });

  it("lists deleted sessions from the recycle-bin endpoint", async () => {
    await sessionApi.getDeletedSessions();
    expect(mocks.get).toHaveBeenCalledWith("/sessions/deleted");
  });

  it("restores a session via POST /restore", async () => {
    await sessionApi.restoreSession("s1");
    expect(mocks.post).toHaveBeenCalledWith("/sessions/s1/restore", {});
  });

  it("purges a session via POST /purge", async () => {
    await sessionApi.purgeSession("s1");
    expect(mocks.post).toHaveBeenCalledWith("/sessions/s1/purge", {});
  });
});

describe("sessionApi.streamSessions", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("keeps the list stream open when the server sends an empty ping", async () => {
    const encoder = new TextEncoder();
    let streamController: ReadableStreamDefaultController<Uint8Array> | null = null;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        streamController = controller;
        controller.enqueue(
          encoder.encode(
            'event: sessions\ndata: {"sessions":[{"session_id":"s1","title":"Session 1","latest_message":"Ready","latest_message_at":"2026-08-19T00:00:00Z","status":"completed","unread_message_count":0}]}\n\n',
          ),
        );
        controller.enqueue(encoder.encode("event: ping\ndata: \n\n"));
      },
    });

    mocks.createSSEStream.mockImplementation(
      async (_endpoint: string, _data: unknown, options?: RequestOptions) => {
        options?.signal?.addEventListener(
          "abort",
          () => {
            streamController?.error(new DOMException("Aborted", "AbortError"));
          },
          { once: true },
        );
        return stream;
      },
    );

    const onSessions = vi.fn();
    const onError = vi.fn();
    const cleanup = sessionApi.streamSessions(onSessions, onError);

    await vi.waitFor(() => {
      expect(onSessions).toHaveBeenCalledWith([
        {
          session_id: "s1",
          title: "Session 1",
          latest_message: "Ready",
          latest_message_at: "2026-08-19T00:00:00Z",
          status: "completed",
          unread_message_count: 0,
        },
      ]);
    });
    await Promise.resolve();

    expect(onError).not.toHaveBeenCalled();

    cleanup();
    await Promise.resolve();
  });
});
