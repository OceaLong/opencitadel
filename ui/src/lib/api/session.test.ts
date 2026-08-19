// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

import type { RequestOptions } from "./fetch";

const mocks = vi.hoisted(() => ({
  createSSEStream: vi.fn(),
}));

vi.mock("./fetch", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./fetch")>();
  return {
    ...actual,
    createSSEStream: mocks.createSSEStream,
  };
});

import { sessionApi } from "./session";

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
