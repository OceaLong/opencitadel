import { timingSafeEqual } from "node:crypto";
import http from "node:http";
import { pathToFileURL } from "node:url";

import {
  ProviderRequestError,
  ProviderScenarioSignal,
  completeChat,
  streamChat,
} from "./lib/chat.mjs";
import { embeddingsFor } from "./lib/embedding.mjs";

const BODY_LIMIT_BYTES = 1024 * 1024;
const DEFAULT_TIMEOUT_DELAY_MS = 310_000;
const MODEL_IDS = [
  "acceptance-chat",
  "acceptance-embedding-1536",
  "acceptance-failure",
];
const CHAT_PARAMETERS = new Set([
  "model",
  "messages",
  "temperature",
  "max_tokens",
  "tools",
  "tool_choice",
  "parallel_tool_calls",
  "response_format",
  "stream",
  "stream_options",
]);
const EMBEDDING_PARAMETERS = new Set([
  "model",
  "input",
  "dimensions",
  "encoding_format",
  "user",
]);

class HttpRequestError extends Error {
  constructor(status, code, message, param = null) {
    super(message);
    this.name = "HttpRequestError";
    this.status = status;
    this.code = code;
    this.param = param;
  }
}

function errorEnvelope(error) {
  return {
    error: {
      message: error.message,
      type: error.status >= 500 ? "server_error" : "invalid_request_error",
      param: error.param ?? null,
      code: error.code,
    },
  };
}

function writeJson(response, status, payload) {
  const encoded = JSON.stringify(payload);
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(encoded),
    "Cache-Control": "no-store",
  });
  response.end(encoded);
}

function bearerMatches(header, token) {
  if (typeof header !== "string" || !header.startsWith("Bearer ")) return false;
  const supplied = Buffer.from(header.slice("Bearer ".length), "utf8");
  const expected = Buffer.from(token, "utf8");
  return supplied.length === expected.length && timingSafeEqual(supplied, expected);
}

function requireAuthorization(request, token) {
  if (!bearerMatches(request.headers.authorization, token)) {
    throw new HttpRequestError(401, "invalid_api_key", "invalid bearer token");
  }
}

async function readJson(request) {
  const contentType = request.headers["content-type"] ?? "";
  if (!contentType.toLowerCase().startsWith("application/json")) {
    throw new HttpRequestError(415, "unsupported_media_type", "Content-Type must be application/json");
  }

  const chunks = [];
  let total = 0;
  for await (const chunk of request) {
    total += chunk.length;
    if (total > BODY_LIMIT_BYTES) {
      throw new HttpRequestError(413, "request_too_large", "request body exceeds 1048576 bytes");
    }
    chunks.push(chunk);
  }
  if (chunks.length === 0) {
    throw new HttpRequestError(400, "invalid_json", "request body must contain JSON");
  }
  try {
    const parsed = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new HttpRequestError(400, "invalid_json", "request JSON must be an object");
    }
    return parsed;
  } catch (error) {
    if (error instanceof HttpRequestError) throw error;
    throw new HttpRequestError(400, "invalid_json", "request body contains malformed JSON");
  }
}

function rejectUnsupportedParameters(body, allowed) {
  const unsupported = Object.keys(body).filter((key) => !allowed.has(key)).sort();
  if (unsupported.length > 0) {
    throw new HttpRequestError(
      422,
      "unsupported_parameter",
      `unsupported parameter: ${unsupported[0]}`,
      unsupported[0],
    );
  }
}

function writeStream(response, chunks) {
  response.writeHead(200, {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache, no-transform",
    Connection: "keep-alive",
    "X-Accel-Buffering": "no",
  });
  for (const chunk of chunks) {
    response.write(`data: ${typeof chunk === "string" ? chunk : JSON.stringify(chunk)}\n\n`);
  }
  response.end();
}

function modelsPayload() {
  return {
    object: "list",
    data: MODEL_IDS.map((id) => ({
      id,
      object: "model",
      created: 0,
      owned_by: "opencitadel-acceptance",
    })),
  };
}

async function handleChat(request, response, timeoutDelayMs) {
  const body = await readJson(request);
  rejectUnsupportedParameters(body, CHAT_PARAMETERS);
  try {
    if (body.stream === true) {
      writeStream(response, streamChat(body));
      return;
    }
    writeJson(response, 200, completeChat(body));
  } catch (error) {
    if (error instanceof ProviderScenarioSignal && error.scenario === "timeout") {
      await new Promise((resolve) => setTimeout(resolve, timeoutDelayMs));
      throw new HttpRequestError(504, "deterministic_timeout", "deterministic provider timeout");
    }
    throw error;
  }
}

async function handleEmbeddings(request, response) {
  const body = await readJson(request);
  rejectUnsupportedParameters(body, EMBEDDING_PARAMETERS);
  if (body.model !== "acceptance-embedding-1536") {
    throw new HttpRequestError(404, "unknown_model", `unknown model: ${String(body.model)}`, "model");
  }
  if (body.dimensions !== undefined && body.dimensions !== 1536) {
    throw new HttpRequestError(422, "invalid_dimensions", "dimensions must be 1536", "dimensions");
  }
  if (
    body.encoding_format !== undefined &&
    body.encoding_format !== "float" &&
    body.encoding_format !== "base64"
  ) {
    throw new HttpRequestError(
      422,
      "invalid_encoding_format",
      "encoding_format must be float or base64",
      "encoding_format",
    );
  }
  let vectors;
  try {
    vectors = embeddingsFor(body.input, 1536);
  } catch (error) {
    throw new HttpRequestError(422, "invalid_input", error.message, "input");
  }
  const inputs = typeof body.input === "string" ? [body.input] : body.input;
  const promptTokens = inputs.reduce(
    (total, value) => total + Math.max(1, Math.ceil(Buffer.byteLength(value, "utf8") / 4)),
    0,
  );
  writeJson(response, 200, {
    object: "list",
    data: vectors.map((embedding, index) => ({ object: "embedding", embedding, index })),
    model: body.model,
    usage: { prompt_tokens: promptTokens, total_tokens: promptTokens },
  });
}

async function route(request, response, token, timeoutDelayMs) {
  const url = new URL(request.url ?? "/", "http://acceptance-inference");
  if (request.method === "GET" && url.pathname === "/healthz") {
    writeJson(response, 200, { status: "ok" });
    return;
  }

  requireAuthorization(request, token);
  if (request.method === "GET" && url.pathname === "/v1/models") {
    writeJson(response, 200, modelsPayload());
    return;
  }
  if (request.method === "POST" && url.pathname === "/v1/chat/completions") {
    await handleChat(request, response, timeoutDelayMs);
    return;
  }
  if (request.method === "POST" && url.pathname === "/v1/embeddings") {
    await handleEmbeddings(request, response);
    return;
  }
  throw new HttpRequestError(404, "not_found", `route not found: ${request.method} ${url.pathname}`);
}

export function createServer({
  token = "acceptance-provider-token",
  timeoutDelayMs = DEFAULT_TIMEOUT_DELAY_MS,
} = {}) {
  if (typeof token !== "string" || token.length === 0) {
    throw new TypeError("provider token must be a non-empty string");
  }
  if (!Number.isInteger(timeoutDelayMs) || timeoutDelayMs < 0) {
    throw new TypeError("timeoutDelayMs must be a non-negative integer");
  }
  return http.createServer(async (request, response) => {
    try {
      await route(request, response, token, timeoutDelayMs);
    } catch (error) {
      if (response.headersSent) {
        response.destroy(error);
        return;
      }
      if (error instanceof ProviderRequestError || error instanceof HttpRequestError) {
        writeJson(response, error.status, errorEnvelope(error));
        return;
      }
      const internal = new HttpRequestError(500, "internal_error", "internal provider error");
      writeJson(response, internal.status, errorEnvelope(internal));
    }
  });
}

const invokedPath = process.argv[1] ? pathToFileURL(process.argv[1]).href : null;
if (invokedPath === import.meta.url) {
  const token = process.env.ACCEPTANCE_PROVIDER_TOKEN;
  if (!token) throw new Error("ACCEPTANCE_PROVIDER_TOKEN is required");
  const port = Number.parseInt(process.env.PORT ?? "8080", 10);
  const server = createServer({ token });
  server.listen(port, "0.0.0.0");
}
