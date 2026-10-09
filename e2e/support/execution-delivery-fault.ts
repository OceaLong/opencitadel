/** Page-local, one-shot delivery injection. Every delivered byte came from upstream.
 * This function is self-contained so Playwright can serialize it into the page.
 */
export function installExecutionDeliveryFault(options: { streamUrl: string }) {
  const original = globalThis.fetch;
  const target = new URL(options.streamUrl);
  const state = {
    duplicated: 0,
    disconnected: 0,
    resumes: [] as string[],
    cursor: "",
    frameSha256: "",
    requests: 0,
  };
  let selected = false;
  let selecting = false;
  let restored = false;
  const deadline = Date.now() + 60_000;
  const readers = new Set<ReadableStreamDefaultReader<Uint8Array>>();
  const wrapped: typeof fetch = async (input, init) => {
    const request = new Request(input, init);
    const url = new URL(request.url);
    if (
      restored ||
      request.method !== "GET" ||
      url.origin !== target.origin ||
      url.pathname !== target.pathname
    )
      return original(input, init);
    state.requests++;
    if (selected) {
      state.resumes.push(
        request.headers.get("Last-Event-ID") ??
          url.searchParams.get("after") ??
          "",
      );
      return original(input, init);
    }
    if (selecting) return original(input, init);
    selecting = true;
    let response: Response;
    try {
      response = await original(input, init);
    } finally {
      selecting = false;
    }
    if (
      !response.ok ||
      !response.body ||
      !response.headers.get("content-type")?.includes("text/event-stream")
    )
      return response;
    if (Date.now() > deadline)
      throw new Error("owned delivery fault expired before selection");
    selected = true;
    const reader = response.body.getReader();
    readers.add(reader);
    let buffered = new Uint8Array(0);
    const body = new ReadableStream<Uint8Array>({
      async pull(controller) {
        try {
          while (true) {
            if (Date.now() > deadline)
              throw new Error(
                "owned delivery fault expired before complete frame",
              );
            // Locate a complete wire frame without re-encoding its bytes.
            let end = -1;
            for (let index = 0; index < buffered.length - 1; index++) {
              if (buffered[index] === 10 && buffered[index + 1] === 10) {
                end = index + 2;
                break;
              }
              if (
                buffered[index] === 13 &&
                buffered[index + 1] === 10 &&
                buffered[index + 2] === 13 &&
                buffered[index + 3] === 10
              ) {
                end = index + 4;
                break;
              }
            }
            if (end >= 0) {
              const frame = buffered.slice(0, end);
              buffered = buffered.slice(end);
              const text = new TextDecoder().decode(frame);
              const cursor = /^id:\s*(.+)\r?$/m.exec(text)?.[1]?.trim();
              if (
                /^event:\s*execution\r?$/m.test(text) &&
                cursor &&
                /^data:/m.test(text)
              ) {
                state.cursor = cursor;
                const digest = await crypto.subtle.digest("SHA-256", frame);
                state.frameSha256 = Array.from(new Uint8Array(digest), (byte) =>
                  byte.toString(16).padStart(2, "0"),
                ).join("");
                controller.enqueue(frame);
                controller.enqueue(frame.slice());
                state.duplicated++;
                await reader.cancel("owned one-shot delivery disconnect");
                readers.delete(reader);
                state.disconnected++;
                controller.close();
                return;
              }
              controller.enqueue(frame);
              return;
            }
            const chunk = await reader.read();
            if (chunk.done) {
              if (buffered.length) controller.enqueue(buffered);
              readers.delete(reader);
              controller.close();
              return;
            }
            if (buffered.length + chunk.value.length > 1024 * 1024)
              throw new Error("owned delivery frame exceeds bound");
            const next = new Uint8Array(buffered.length + chunk.value.length);
            next.set(buffered);
            next.set(chunk.value, buffered.length);
            buffered = next;
          }
        } catch (error) {
          readers.delete(reader);
          await reader.cancel().catch(() => undefined);
          controller.error(error);
        }
      },
      async cancel(reason) {
        readers.delete(reader);
        await reader.cancel(reason);
      },
    });
    return new Response(body, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  };
  globalThis.fetch = wrapped;
  const control = {
    state,
    async restore() {
      restored = true;
      if (globalThis.fetch === wrapped) globalThis.fetch = original;
      await Promise.all(
        [...readers].map((reader) => reader.cancel("owned fault teardown")),
      );
      readers.clear();
    },
  };
  Object.assign(globalThis, { __executionDeliveryFault: control });
  return state;
}
