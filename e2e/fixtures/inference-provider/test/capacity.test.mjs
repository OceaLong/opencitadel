import assert from "node:assert/strict";
import { Readable } from "node:stream";
import test from "node:test";
import { performance } from "node:perf_hooks";
import { createServer } from "../server.mjs";

// Exercise the actual HTTP request listener without binding a socket or starting a service.
async function dispatch(body) {
  const server = createServer({ token: "capacity-unit-only" });
  const request = Readable.from([Buffer.from(JSON.stringify(body))]);
  request.method = "POST";
  request.url = "/v1/chat/completions";
  request.headers = { authorization: "Bearer capacity-unit-only", "content-type": "application/json" };
  const start = performance.now();
  return await new Promise((resolve, reject) => {
    const chunks = [];
    const response = {
      headersSent: false,
      writeHead(status, headers) { this.status = status; this.headers = headers; this.headersSent = true; },
      write(value) { chunks.push(value); },
      end(value) { if (value) chunks.push(value); resolve({ status: this.status, text: chunks.join(""), elapsed: performance.now() - start, headers: this.headers }); },
      destroy: reject,
    };
    server.emit("request", request, response);
  });
}

for (const stream of [false, true]) {
  test(`capacity successful profile delays actual ${stream ? "stream" : "JSON"} response`, async () => {
    const result = await dispatch({ model: "acceptance-capacity", messages: [{ role: "user", content: "[acceptance:evaluation:rule-pass]" }], stream });
    assert.equal(result.status, 200, result.text);
    assert.ok(result.elapsed >= 95, `fixed 100 ms delay missing: ${result.elapsed}`);
    assert.ok(result.elapsed < 5000, "bounded successful response did not complete");
    assert.match(result.text, /Acceptance response:/);
    assert.match(result.text, /usage/);
    assert.equal(result.headers["X-Acceptance-Delay-Ms"], "100");
  });
}

test("invalid capacity requests fail before the successful-delay path", async () => {
  const result = await dispatch({ model: "acceptance-capacity", messages: [] });
  assert.equal(result.status, 422);
  assert.equal(result.headers["X-Acceptance-Delay-Ms"], undefined);
});

test("ordinary successful model has no capacity profile marker", async () => {
  const result = await dispatch({ model: "acceptance-chat", messages: [{ role: "user", content: "ordinary" }] });
  assert.equal(result.status, 200);
  assert.equal(result.headers["X-Acceptance-Delay-Ms"], undefined);
});
