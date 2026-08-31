import assert from "node:assert/strict";
import { once } from "node:events";
import test from "node:test";

import { createServer } from "../server.mjs";

const TOKEN = "acceptance-provider-token";

async function withServer(run, options = {}) {
  const server = createServer({ token: TOKEN, timeoutDelayMs: 5, ...options });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  assert.ok(address && typeof address === "object");
  try {
    await run(`http://127.0.0.1:${address.port}`);
  } finally {
    server.close();
    await once(server, "close");
  }
}

function authorized(init = {}) {
  return {
    ...init,
    headers: {
      Authorization: `Bearer ${TOKEN}`,
      ...init.headers,
    },
  };
}

async function errorPayload(response) {
  const body = await response.json();
  assert.deepEqual(Object.keys(body), ["error"]);
  return body.error;
}

test("health is public and models require the exact bearer token", async () => {
  await withServer(async (baseUrl) => {
    const health = await fetch(`${baseUrl}/healthz`);
    assert.equal(health.status, 200);
    assert.deepEqual(await health.json(), { status: "ok" });

    const unauthorized = await fetch(`${baseUrl}/v1/models`);
    assert.equal(unauthorized.status, 401);
    assert.equal((await errorPayload(unauthorized)).code, "invalid_api_key");

    const models = await fetch(`${baseUrl}/v1/models`, authorized());
    assert.equal(models.status, 200);
    const payload = await models.json();
    assert.deepEqual(
      payload.data.map((model) => model.id),
      ["acceptance-chat", "acceptance-embedding-1536", "acceptance-failure"],
    );
    assert.ok(payload.data.every((model) => model.object === "model" && model.owned_by === "opencitadel-acceptance"));
  });
});

test("non-streaming and streaming chat use OpenAI protocol envelopes", async () => {
  await withServer(async (baseUrl) => {
    const body = {
      model: "acceptance-chat",
      messages: [{ role: "user", content: "hello HTTP" }],
      temperature: 0,
      max_tokens: 64,
    };
    const completion = await fetch(
      `${baseUrl}/v1/chat/completions`,
      authorized({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }),
    );
    assert.equal(completion.status, 200);
    assert.equal((await completion.json()).choices[0].message.content, "Acceptance response: hello HTTP");

    const stream = await fetch(
      `${baseUrl}/v1/chat/completions`,
      authorized({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...body, stream: true, stream_options: { include_usage: true } }),
      }),
    );
    assert.equal(stream.status, 200);
    assert.match(stream.headers.get("content-type"), /^text\/event-stream/);
    const frames = (await stream.text())
      .trim()
      .split("\n\n")
      .map((frame) => frame.replace(/^data: /, ""));
    assert.equal(frames.at(-1), "[DONE]");
    assert.equal(JSON.parse(frames[1]).choices[0].delta.content, "Acceptance response: hello HTTP");
  });
});

test("embeddings preserve batch order and always contain 1536 finite floats", async () => {
  await withServer(async (baseUrl) => {
    const response = await fetch(
      `${baseUrl}/v1/embeddings`,
      authorized({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: "acceptance-embedding-1536",
          input: ["first", "second"],
          encoding_format: "base64",
        }),
      }),
    );
    assert.equal(response.status, 200);
    const payload = await response.json();
    assert.deepEqual(payload.data.map((item) => item.index), [0, 1]);
    assert.ok(payload.data.every((item) => item.embedding.length === 1536));
    assert.ok(payload.data.flatMap((item) => item.embedding).every(Number.isFinite));
    assert.equal(payload.usage.total_tokens, payload.usage.prompt_tokens);
  });
});

test("invalid paths, JSON, models, histories, and parameters return stable 4xx errors", async () => {
  await withServer(async (baseUrl) => {
    const missing = await fetch(`${baseUrl}/v1/missing`, authorized());
    assert.equal(missing.status, 404);
    assert.equal((await errorPayload(missing)).code, "not_found");

    const malformed = await fetch(
      `${baseUrl}/v1/chat/completions`,
      authorized({ method: "POST", headers: { "Content-Type": "application/json" }, body: "{" }),
    );
    assert.equal(malformed.status, 400);
    assert.equal((await errorPayload(malformed)).code, "invalid_json");

    const cases = [
      {
        status: 404,
        code: "unknown_model",
        body: { model: "missing", messages: [{ role: "user", content: "hello" }] },
      },
      {
        status: 422,
        code: "unsupported_parameter",
        body: {
          model: "acceptance-chat",
          messages: [{ role: "user", content: "hello" }],
          seed: 5,
        },
      },
      {
        status: 422,
        code: "invalid_tool_history",
        body: {
          model: "acceptance-chat",
          messages: [{ role: "tool", tool_call_id: "call_missing", content: "{}" }],
        },
      },
    ];
    for (const item of cases) {
      const response = await fetch(
        `${baseUrl}/v1/chat/completions`,
        authorized({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(item.body),
        }),
      );
      assert.equal(response.status, item.status);
      assert.equal((await errorPayload(response)).code, item.code);
    }
  });
});

test("request size and deterministic timeout scenarios are bounded", async () => {
  await withServer(async (baseUrl) => {
    const oversized = await fetch(
      `${baseUrl}/v1/chat/completions`,
      authorized({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: "acceptance-chat",
          messages: [{ role: "user", content: "x".repeat(1_048_576) }],
        }),
      }),
    );
    assert.equal(oversized.status, 413);
    assert.equal((await errorPayload(oversized)).code, "request_too_large");

    const timeout = await fetch(
      `${baseUrl}/v1/chat/completions`,
      authorized({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: "acceptance-failure",
          messages: [{ role: "user", content: "[acceptance:timeout]" }],
        }),
      }),
    );
    assert.equal(timeout.status, 504);
    assert.equal((await errorPayload(timeout)).code, "deterministic_timeout");
  });
});
