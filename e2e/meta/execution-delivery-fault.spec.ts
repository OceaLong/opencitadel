import { expect, test } from "@playwright/test";
import { installExecutionDeliveryFault } from "../support/execution-delivery-fault";

const target = "http://owned.test/api/execution-runs/owned/events/stream";
const frame =
  'event: execution\nid: opaque-1\ndata: {"actual":"unaltered"}\n\n';
const later = 'event: execution\nid: opaque-2\ndata: {"actual":"later"}\n\n';
function control(): any {
  return (globalThis as any).__executionDeliveryFault;
}

test("duplicates a complete upstream execution frame byte-for-byte, disconnects and delegates resume", async () => {
  const original = globalThis.fetch;
  let cancelled = 0;
  let calls = 0;
  globalThis.fetch = (async () => {
    calls++;
    return new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(
            new TextEncoder().encode(": heartbeat\n\n" + frame.slice(0, 17)),
          );
          controller.enqueue(new TextEncoder().encode(frame.slice(17) + later));
          controller.close();
        },
        cancel() {
          cancelled++;
        },
      }),
      {
        headers: {
          "content-type": "text/event-stream",
          "x-real-response": "preserved",
        },
      },
    );
  }) as typeof fetch;
  try {
    installExecutionDeliveryFault({ streamUrl: target });
    const response = await fetch(target);
    expect(response.headers.get("x-real-response")).toBe("preserved");
    expect(await response.text()).toBe(": heartbeat\n\n" + frame + frame);
    expect(cancelled).toBeLessThanOrEqual(1);
    const resume = await fetch(target, {
      headers: { "Last-Event-ID": "opaque-1" },
    });
    await resume.body!.cancel();
    expect(calls).toBe(2);
    expect(control().state).toMatchObject({
      duplicated: 1,
      disconnected: 1,
      resumes: ["opaque-1"],
    });
  } finally {
    await control()?.restore();
    globalThis.fetch = original;
  }
});

test("foreign URL and failed response are never altered or counted as injected delivery", async () => {
  const original = globalThis.fetch;
  let response = new Response("denied", { status: 403 });
  globalThis.fetch = (async () => response) as typeof fetch;
  try {
    installExecutionDeliveryFault({ streamUrl: target });
    expect(await fetch(target)).toBe(response);
    response = new Response(frame, {
      headers: { "content-type": "text/event-stream" },
    });
    expect(await fetch(target.replace("owned/events", "foreign/events"))).toBe(
      response,
    );
    expect(control().state.duplicated).toBe(0);
    expect(control().state.disconnected).toBe(0);
  } finally {
    await control()?.restore();
    globalThis.fetch = original;
  }
});

test("upstream failure and incomplete data do not fabricate a terminal or a complete frame", async () => {
  const original = globalThis.fetch;
  globalThis.fetch = (async () =>
    new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode(frame.slice(0, -2)));
          controller.error(new Error("upstream failed"));
        },
      }),
      { headers: { "content-type": "text/event-stream" } },
    )) as typeof fetch;
  try {
    installExecutionDeliveryFault({ streamUrl: target });
    await expect((await fetch(target)).text()).rejects.toThrow(
      "upstream failed",
    );
    expect(control().state.duplicated).toBe(0);
  } finally {
    await control()?.restore();
    globalThis.fetch = original;
  }
});

test("concurrent exact subscriptions spend the duplication budget only once", async () => {
  const original = globalThis.fetch;
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  globalThis.fetch = (async () => {
    await gate;
    return new Response(frame, {
      headers: { "content-type": "text/event-stream" },
    });
  }) as typeof fetch;
  try {
    installExecutionDeliveryFault({ streamUrl: target });
    const one = fetch(target),
      two = fetch(target);
    release();
    const bodies = await Promise.all(
      [one, two].map(async (response) => (await response).text()),
    );
    expect(bodies.sort()).toEqual([frame, frame + frame].sort());
    expect(control().state.duplicated).toBe(1);
  } finally {
    await control()?.restore();
    globalThis.fetch = original;
  }
});
