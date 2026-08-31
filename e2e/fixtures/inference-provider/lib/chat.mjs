import { canonicalDigest, canonicalJson } from "./canonical.mjs";

const CHAT_MODELS = new Set(["acceptance-chat", "acceptance-failure"]);
const TOOL_MARKER = /\[acceptance:tool:([A-Za-z0-9_.:-]+)\]/;

export class ProviderRequestError extends Error {
  constructor(status, code, message, param = null) {
    super(message);
    this.name = "ProviderRequestError";
    this.status = status;
    this.code = code;
    this.param = param;
  }
}

export class ProviderScenarioSignal extends Error {
  constructor(scenario, delayMs) {
    super(`acceptance scenario requires ${scenario}`);
    this.name = "ProviderScenarioSignal";
    this.scenario = scenario;
    this.delayMs = delayMs;
  }
}

function requestError(status, code, message, param = null) {
  throw new ProviderRequestError(status, code, message, param);
}

function contentText(content) {
  if (typeof content === "string") return content;
  if (content === null) return "";
  if (!Array.isArray(content)) {
    requestError(422, "invalid_messages", "message content must be text, parts, or null", "messages");
  }
  return content
    .map((part) => {
      if (!part || typeof part !== "object") {
        requestError(422, "invalid_messages", "message content part must be an object", "messages");
      }
      if (part.type === "text" && typeof part.text === "string") return part.text;
      if (part.type === "image_url") return "[image]";
      requestError(422, "invalid_messages", `unsupported message part: ${String(part.type)}`, "messages");
    })
    .join("\n");
}

function validateMessages(messages) {
  if (!Array.isArray(messages) || messages.length === 0) {
    requestError(422, "invalid_messages", "messages must be a non-empty array", "messages");
  }
  for (const message of messages) {
    if (!message || typeof message !== "object") {
      requestError(422, "invalid_messages", "each message must be an object", "messages");
    }
    if (!["system", "user", "assistant", "tool"].includes(message.role)) {
      requestError(422, "invalid_messages", `unsupported message role: ${String(message.role)}`, "messages");
    }
    contentText(message.content ?? null);
  }
}

function lastUserText(messages) {
  const message = [...messages].reverse().find((entry) => entry.role === "user");
  return message ? contentText(message.content) : "acceptance";
}

function tokenCount(value) {
  return Math.max(1, Math.ceil(Buffer.byteLength(value, "utf8") / 4));
}

function usageFor(request, message) {
  const promptText = canonicalJson(request.messages);
  const completionText = canonicalJson(message);
  const promptTokens = tokenCount(promptText);
  const completionTokens = tokenCount(completionText);
  return {
    prompt_tokens: promptTokens,
    completion_tokens: completionTokens,
    total_tokens: promptTokens + completionTokens,
  };
}

function valueForSchema(schema) {
  if (!schema || typeof schema !== "object") return "acceptance";
  if (Object.hasOwn(schema, "const")) return schema.const;
  if (Array.isArray(schema.enum) && schema.enum.length > 0) return schema.enum[0];
  if (Array.isArray(schema.oneOf) && schema.oneOf.length > 0) return valueForSchema(schema.oneOf[0]);
  if (Array.isArray(schema.anyOf) && schema.anyOf.length > 0) {
    const candidate = schema.anyOf.find((entry) => entry?.type !== "null") ?? schema.anyOf[0];
    return valueForSchema(candidate);
  }

  const type = Array.isArray(schema.type)
    ? schema.type.find((entry) => entry !== "null") ?? "null"
    : schema.type;
  if (type === "object" || (!type && schema.properties)) {
    const properties = schema.properties ?? {};
    const required = Array.isArray(schema.required) ? schema.required : Object.keys(properties);
    return Object.fromEntries(
      [...required].sort().map((key) => [key, valueForSchema(properties[key])]),
    );
  }
  if (type === "array") return [valueForSchema(schema.items)];
  if (type === "integer" || type === "number") return 1;
  if (type === "boolean") return true;
  if (type === "null") return null;
  return "acceptance";
}

function structuredContent(responseFormat) {
  if (responseFormat?.type === "json_object") {
    return canonicalJson({ status: "accepted", summary: "acceptance" });
  }
  if (responseFormat?.type === "json_schema") {
    const schema = responseFormat.json_schema?.schema;
    if (!schema || typeof schema !== "object") {
      requestError(422, "invalid_response_format", "json_schema.schema is required", "response_format");
    }
    return canonicalJson(valueForSchema(schema));
  }
  requestError(
    422,
    "invalid_response_format",
    `unsupported response format: ${String(responseFormat?.type)}`,
    "response_format",
  );
}

function toolDefinitions(tools) {
  if (tools === undefined) return new Map();
  if (!Array.isArray(tools)) requestError(422, "invalid_tools", "tools must be an array", "tools");
  const declared = new Map();
  for (const tool of tools) {
    const name = tool?.type === "function" ? tool.function?.name : null;
    if (typeof name !== "string" || name.length === 0 || declared.has(name)) {
      requestError(422, "invalid_tools", "tools must have unique function names", "tools");
    }
    declared.set(name, tool);
  }
  return declared;
}

function continuationMessage(messages) {
  const last = messages.at(-1);
  if (last.role !== "tool") return null;
  if (typeof last.tool_call_id !== "string" || last.tool_call_id.length === 0) {
    requestError(422, "invalid_tool_history", "tool result requires tool_call_id", "messages");
  }
  const matched = messages.slice(0, -1).some(
    (message) =>
      message.role === "assistant" &&
      Array.isArray(message.tool_calls) &&
      message.tool_calls.some((call) => call?.id === last.tool_call_id),
  );
  if (!matched) {
    requestError(
      422,
      "invalid_tool_history",
      "tool result requires a matching assistant tool call",
      "messages",
    );
  }
  return {
    role: "assistant",
    content: `Acceptance tool result: ${contentText(last.content)}`,
  };
}

function selectedToolMessage(request, digest) {
  const text = lastUserText(request.messages);
  const match = TOOL_MARKER.exec(text);
  if (!match) return null;

  const declared = toolDefinitions(request.tools);
  const name = match[1];
  const tool = declared.get(name);
  if (!tool) requestError(422, "undeclared_tool", `requested tool is not declared: ${name}`, "tools");
  const args = valueForSchema(tool.function.parameters ?? { type: "object", properties: {} });
  return {
    role: "assistant",
    content: null,
    tool_calls: [
      {
        id: `call_${digest.slice(0, 24)}`,
        type: "function",
        function: {
          name,
          arguments: canonicalJson(args),
        },
      },
    ],
  };
}

function failureScenario(request) {
  if (request.model !== "acceptance-failure") return null;
  const text = lastUserText(request.messages);
  if (text.includes("[acceptance:retryable]")) {
    requestError(503, "retryable", "deterministic retryable provider failure");
  }
  if (text.includes("[acceptance:terminal]")) {
    requestError(422, "terminal", "deterministic terminal provider failure");
  }
  if (text.includes("[acceptance:timeout]")) {
    throw new ProviderScenarioSignal("timeout", 310_000);
  }
  return text.includes("[acceptance:empty]") ? "empty" : null;
}

function completionFor(request, message, finishReason) {
  const digest = canonicalDigest(request);
  return {
    id: `chatcmpl-${digest.slice(0, 24)}`,
    object: "chat.completion",
    created: 0,
    model: request.model,
    choices: [{ index: 0, message, finish_reason: finishReason }],
    usage: usageFor(request, message),
  };
}

export function completeChat(request) {
  if (!request || typeof request !== "object" || Array.isArray(request)) {
    requestError(400, "invalid_request", "request body must be an object");
  }
  if (!CHAT_MODELS.has(request.model)) {
    requestError(404, "unknown_model", `unknown model: ${String(request.model)}`, "model");
  }
  validateMessages(request.messages);

  const failure = failureScenario(request);
  if (failure === "empty") {
    return {
      id: `chatcmpl-${canonicalDigest(request).slice(0, 24)}`,
      object: "chat.completion",
      created: 0,
      model: request.model,
      choices: [],
      usage: { prompt_tokens: 1, completion_tokens: 0, total_tokens: 1 },
    };
  }

  const continuation = continuationMessage(request.messages);
  if (continuation) return completionFor(request, continuation, "stop");

  const digest = canonicalDigest(request);
  const toolMessage = selectedToolMessage(request, digest);
  if (toolMessage) return completionFor(request, toolMessage, "tool_calls");

  toolDefinitions(request.tools);
  const content = request.response_format
    ? structuredContent(request.response_format)
    : `Acceptance response: ${lastUserText(request.messages)}`;
  return completionFor(request, { role: "assistant", content }, "stop");
}

function chunkBase(completion) {
  return {
    id: completion.id,
    object: "chat.completion.chunk",
    created: completion.created,
    model: completion.model,
  };
}

export function streamChat(request) {
  const completion = completeChat(request);
  const base = chunkBase(completion);
  if (completion.choices.length === 0) {
    return [{ ...base, choices: [], usage: completion.usage }, "[DONE]"];
  }

  const choice = completion.choices[0];
  const chunks = [
    {
      ...base,
      choices: [{ index: 0, delta: { role: "assistant" }, finish_reason: null }],
    },
  ];
  if (choice.message.tool_calls) {
    chunks.push({
      ...base,
      choices: [
        {
          index: 0,
          delta: {
            tool_calls: choice.message.tool_calls.map((call, index) => ({ ...call, index })),
          },
          finish_reason: null,
        },
      ],
    });
  } else {
    chunks.push({
      ...base,
      choices: [{ index: 0, delta: { content: choice.message.content }, finish_reason: null }],
    });
  }
  chunks.push({
    ...base,
    choices: [{ index: 0, delta: {}, finish_reason: choice.finish_reason }],
  });
  chunks.push({ ...base, choices: [], usage: completion.usage });
  chunks.push("[DONE]");
  return chunks;
}
