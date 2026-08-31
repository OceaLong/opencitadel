import assert from "node:assert/strict";
import test from "node:test";

import {
  ProviderRequestError,
  ProviderScenarioSignal,
  completeChat,
  streamChat,
} from "../lib/chat.mjs";

function request(overrides = {}) {
  return {
    model: "acceptance-chat",
    messages: [{ role: "user", content: "hello acceptance" }],
    temperature: 0,
    max_tokens: 256,
    ...overrides,
  };
}

test("completeChat returns a stable ordinary OpenAI completion", () => {
  const completion = completeChat(request());

  assert.match(completion.id, /^chatcmpl-[0-9a-f]{24}$/);
  assert.equal(completion.created, 0);
  assert.equal(completion.model, "acceptance-chat");
  assert.deepEqual(completion.choices, [
    {
      index: 0,
      message: {
        role: "assistant",
        content: "Acceptance response: hello acceptance",
      },
      finish_reason: "stop",
    },
  ]);
  assert.equal(
    completion.usage.total_tokens,
    completion.usage.prompt_tokens + completion.usage.completion_tokens,
  );
  assert.deepEqual(completeChat(request()), completion);
});

test("completeChat selects only an explicitly requested declared tool", () => {
  const completion = completeChat(
    request({
      messages: [{ role: "user", content: "[acceptance:tool:inspect_service]" }],
      tools: [
        {
          type: "function",
          function: {
            name: "inspect_service",
            description: "Inspect one service",
            parameters: {
              type: "object",
              properties: { query: { type: "string" } },
              required: ["query"],
              additionalProperties: false,
            },
          },
        },
      ],
    }),
  );

  assert.equal(completion.choices[0].finish_reason, "tool_calls");
  assert.deepEqual(completion.choices[0].message.tool_calls, [
    {
      id: completion.choices[0].message.tool_calls[0].id,
      type: "function",
      function: { name: "inspect_service", arguments: '{"query":"acceptance"}' },
    },
  ]);
  assert.match(completion.choices[0].message.tool_calls[0].id, /^call_[0-9a-f]{24}$/);
});

test("completeChat rejects a requested tool that was not declared", () => {
  assert.throws(
    () =>
      completeChat(
        request({
          messages: [{ role: "user", content: "[acceptance:tool:missing_tool]" }],
          tools: [],
        }),
      ),
    (error) =>
      error instanceof ProviderRequestError &&
      error.status === 422 &&
      error.code === "undeclared_tool",
  );
});

test("completeChat continues only after a matching tool result", () => {
  const toolCall = {
    id: "call_0123456789abcdef01234567",
    type: "function",
    function: { name: "inspect_service", arguments: '{"query":"acceptance"}' },
  };
  const completion = completeChat(
    request({
      messages: [
        { role: "user", content: "inspect the service" },
        { role: "assistant", content: null, tool_calls: [toolCall] },
        {
          role: "tool",
          tool_call_id: toolCall.id,
          content: '{"status":"healthy"}',
        },
      ],
    }),
  );

  assert.equal(
    completion.choices[0].message.content,
    'Acceptance tool result: {"status":"healthy"}',
  );
  assert.throws(
    () =>
      completeChat(
        request({
          messages: [
            { role: "assistant", content: null, tool_calls: [toolCall] },
            { role: "tool", tool_call_id: "call_wrong", content: "{}" },
          ],
        }),
      ),
    /matching assistant tool call/,
  );
});

test("completeChat generates deterministic JSON for both response modes", () => {
  const objectCompletion = completeChat(
    request({ response_format: { type: "json_object" } }),
  );
  assert.deepEqual(JSON.parse(objectCompletion.choices[0].message.content), {
    status: "accepted",
    summary: "acceptance",
  });

  const schemaCompletion = completeChat(
    request({
      response_format: {
        type: "json_schema",
        json_schema: {
          name: "AcceptanceRecord",
          strict: true,
          schema: {
            type: "object",
            properties: {
              title: { type: "string" },
              count: { type: "integer" },
            },
            required: ["title", "count"],
            additionalProperties: false,
          },
        },
      },
    }),
  );
  assert.equal(schemaCompletion.choices[0].message.content, '{"count":1,"title":"acceptance"}');
});

test("acceptance-failure exposes explicit retryable, terminal, empty, and timeout cases", () => {
  assert.throws(
    () =>
      completeChat(
        request({
          model: "acceptance-failure",
          messages: [{ role: "user", content: "[acceptance:retryable]" }],
        }),
      ),
    (error) =>
      error instanceof ProviderRequestError && error.status === 503 && error.code === "retryable",
  );
  assert.throws(
    () =>
      completeChat(
        request({
          model: "acceptance-failure",
          messages: [{ role: "user", content: "[acceptance:terminal]" }],
        }),
      ),
    (error) =>
      error instanceof ProviderRequestError && error.status === 422 && error.code === "terminal",
  );
  assert.deepEqual(
    completeChat(
      request({
        model: "acceptance-failure",
        messages: [{ role: "user", content: "[acceptance:empty]" }],
      }),
    ).choices,
    [],
  );
  assert.throws(
    () =>
      completeChat(
        request({
          model: "acceptance-failure",
          messages: [{ role: "user", content: "[acceptance:timeout]" }],
        }),
      ),
    (error) => error instanceof ProviderScenarioSignal && error.scenario === "timeout",
  );
});

test("completeChat rejects unknown models and malformed message histories", () => {
  assert.throws(
    () => completeChat(request({ model: "unknown" })),
    (error) => error instanceof ProviderRequestError && error.code === "unknown_model",
  );
  assert.throws(
    () => completeChat(request({ messages: [] })),
    (error) => error instanceof ProviderRequestError && error.code === "invalid_messages",
  );
});

test("streamChat emits ordered OpenAI chunks and a terminal marker", () => {
  const chunks = streamChat(request({ stream: true, stream_options: { include_usage: true } }));

  assert.deepEqual(chunks[0].choices[0].delta, { role: "assistant" });
  assert.deepEqual(chunks[1].choices[0].delta, {
    content: "Acceptance response: hello acceptance",
  });
  assert.equal(chunks[2].choices[0].finish_reason, "stop");
  assert.deepEqual(chunks[3].choices, []);
  assert.equal(chunks[4], "[DONE]");
});
