import { ACTIVE_WORKSPACE_KEY } from "@/lib/storage-keys";

import { translate } from "@/i18n/translate";

import { dispatchAuthRequired } from "../auth-events";
import type { ApiResponse, SSEEventData, SSEEventHandler } from "./types";

/**
 * API 配置
 */
export const API_CONFIG = {
  baseURL: process.env.NEXT_PUBLIC_API_BASE_URL || "/api",
  timeout: 30000, // 30秒
} as const;

/**
 * 自定义错误类
 */
export class ApiError extends Error {
  constructor(
    public code: number,
    public msg: string,
    public data: unknown = null,
    public errorKey?: string | null,
    public errorParams?: Record<string, string> | null,
  ) {
    super(msg);
    this.name = "ApiError";
  }
}

function resolveApiMessage(body: {
  msg?: string;
  error_key?: string | null;
  error_params?: Record<string, string> | null;
}): string {
  if (body.error_key) {
    return translate(body.error_key, body.error_params ?? undefined);
  }
  return body.msg || translate("errors.unknown");
}

/**
 * 请求选项
 */
export type RequestOptions = RequestInit & {
  timeout?: number;
  /** Snapshot of the authorized workspace for a logical multi-request read. */
  workspaceId?: string;
  skipErrorHandler?: boolean;
  skipAuthRefresh?: boolean;
  skipAuthRedirect?: boolean;
};

function readCookie(name: string): string {
  if (typeof document === "undefined") return "";
  const match = document.cookie
    .split("; ")
    .find((part) => part.startsWith(`${encodeURIComponent(name)}=`));
  return match ? decodeURIComponent(match.split("=").slice(1).join("=")) : "";
}

/**
 * 读取 CSRF token cookie，回显到 `X-CSRF-Token` 请求头。
 *
 * 镜像后端 `read_host_cookie` 的兜底顺序：生产环境（HTTPS）下后端把 CSRF
 * cookie 写成带 `__Host-` 前缀的 `__Host-csrf_token`，开发/http 下仍是裸名
 * `csrf_token`。这里优先读 `__Host-csrf_token`，回退 `csrf_token`，从而两种
 * 部署形态都能正确回显 token。
 */
function readCsrfToken(): string {
  return readCookie("__Host-csrf_token") || readCookie("csrf_token");
}

function activeWorkspaceId(): string {
  if (typeof window === "undefined") return "";
  return window.localStorage.getItem(ACTIVE_WORKSPACE_KEY) ?? "";
}

let refreshPromise: Promise<unknown> | null = null;

async function refreshAuthOnce(): Promise<unknown> {
  if (!refreshPromise) {
    refreshPromise = request("/auth/refresh", {
      method: "POST",
      skipAuthRefresh: true,
      skipAuthRedirect: true,
    }).finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

function buildAuthHeaders(
  method: string = "GET",
  headers: HeadersInit = {},
  workspaceId = activeWorkspaceId(),
): HeadersInit {
  const mergedHeaders: HeadersInit = {
    ...headers,
  };
  const upperMethod = method.toUpperCase();
  const csrfToken = readCsrfToken();
  if (csrfToken && !["GET", "HEAD", "OPTIONS"].includes(upperMethod)) {
    (mergedHeaders as Record<string, string>)["X-CSRF-Token"] = csrfToken;
  }
  if (workspaceId) {
    (mergedHeaders as Record<string, string>)["X-Workspace-Id"] = workspaceId;
  }
  return mergedHeaders;
}

export async function authenticatedFetch(
  input: string,
  options: RequestOptions = {},
): Promise<Response> {
  const url = input.startsWith("http") ? input : `${API_CONFIG.baseURL}${input}`;
  const method = (options.method || "GET").toString().toUpperCase();
  const {
    skipAuthRefresh = false,
    skipAuthRedirect = false,
    workspaceId = activeWorkspaceId(),
    ...fetchOptions
  } = options;
  const response = await fetch(url, {
    ...fetchOptions,
    headers: buildAuthHeaders(method, fetchOptions.headers || {}, workspaceId),
    credentials: "include",
  });
  if (response.status === 401 && !skipAuthRefresh) {
    try {
      await refreshAuthOnce();
    } catch {
      if (!skipAuthRedirect) {
        dispatchAuthRequired();
      }
      return response;
    }
    return fetch(url, {
      ...fetchOptions,
      headers: buildAuthHeaders(method, fetchOptions.headers || {}, workspaceId),
      credentials: "include",
    });
  }
  return response;
}

/**
 * 解析响应
 */
async function parseResponse<T>(response: Response): Promise<ApiResponse<T>> {
  const contentType = response.headers.get("content-type");

  if (contentType?.includes("application/json")) {
    return await response.json();
  }

  // 处理非 JSON 响应（如文件下载）
  const text = await response.text();
  return {
    code: response.ok ? 0 : response.status,
    msg: response.ok ? "success" : text || response.statusText,
    data: text as unknown as T,
  };
}

/**
 * 处理错误响应
 */
async function handleErrorResponse(response: Response): Promise<never> {
  if (response.status === 413) {
    throw new ApiError(413, translate("errors.payloadTooLarge"));
  }

  let errorData: ApiResponse;

  try {
    errorData = await parseResponse(response);
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    errorData = {
      code: response.status,
      msg: response.statusText || translate("errors.requestFailed"),
      data: null,
    };
  }

  throw new ApiError(
    errorData.code,
    resolveApiMessage(errorData),
    errorData.data,
    errorData.error_key,
    errorData.error_params,
  );
}

function isRateLimitError(code: number, errorKey?: string | null): boolean {
  return code === 429 || errorKey === "errors.rateLimit";
}

/**
 * 带超时的 fetch
 */
async function fetchWithTimeout<T>(
  url: string,
  options: RequestOptions = {},
  timeout: number,
  consume: (response: Response) => Promise<T>,
): Promise<T> {
  const controller = new AbortController();
  let timedOut = false;
  const abort = () => controller.abort(options.signal?.reason);
  if (options.signal?.aborted) abort();
  else options.signal?.addEventListener("abort", abort, { once: true });
  const timeoutId = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeout);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    // Fetch resolves at headers; retain cancellation and timeout through the body.
    return await consume(response);
  } catch (error) {
    if (timedOut) throw new ApiError(408, translate("errors.requestTimeout"));
    throw error;
  } finally {
    clearTimeout(timeoutId);
    options.signal?.removeEventListener("abort", abort);
  }
}

/**
 * 核心请求函数
 */
async function request<T = unknown>(
  endpoint: string,
  options: RequestOptions = {},
  workspaceId = options.workspaceId ?? activeWorkspaceId(),
): Promise<T> {
  const url = endpoint.startsWith("http") ? endpoint : `${API_CONFIG.baseURL}${endpoint}`;

  const {
    timeout = API_CONFIG.timeout,
    skipErrorHandler = false,
    skipAuthRefresh = false,
    skipAuthRedirect = false,
    headers = {},
    workspaceId: _workspaceId,
    ...fetchOptions
  } = options;

  void _workspaceId;
  // 合并请求头
  const mergedHeaders: HeadersInit = {
    "Content-Type": "application/json",
    ...headers,
  };
  const method = (fetchOptions.method || "GET").toString().toUpperCase();
  const csrfToken = readCsrfToken();
  if (csrfToken && !["GET", "HEAD", "OPTIONS"].includes(method)) {
    (mergedHeaders as Record<string, string>)["X-CSRF-Token"] = csrfToken;
  }
  if (workspaceId) {
    (mergedHeaders as Record<string, string>)["X-Workspace-Id"] = workspaceId;
  }

  // 如果是 FormData，删除 Content-Type 让浏览器自动设置
  if (fetchOptions.body instanceof FormData) {
    delete (mergedHeaders as Record<string, string>)["Content-Type"];
  }

  try {
    return await fetchWithTimeout<T>(
      url,
      {
        ...fetchOptions,
        headers: mergedHeaders,
        credentials: "include",
      },
      timeout,
      async (response) => {
        // 处理 HTTP 错误状态码
        if (!response.ok) {
          if (response.status === 401 && !skipAuthRefresh) {
            try {
              await refreshAuthOnce();
              return request<T>(endpoint, { ...options, skipAuthRefresh: true }, workspaceId);
            } catch {
              if (!skipAuthRedirect) {
                dispatchAuthRequired();
              }
            }
          }
          if (skipErrorHandler) {
            return parseResponse<T>(response) as Promise<T>;
          }
          await handleErrorResponse(response);
        }

        const result = await parseResponse<T>(response);

        // 处理业务错误（code 不在成功范围内）
        if (result.code !== 0 && result.code !== 200) {
          if (skipErrorHandler) {
            return result.data as T;
          }
          throw new ApiError(
            result.code,
            resolveApiMessage(result),
            result.data,
            result.error_key,
            result.error_params,
          );
        }

        return result.data as T;
      },
    );
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    if (error instanceof ApiError) {
      if (isRateLimitError(error.code, error.errorKey) && typeof window !== "undefined") {
        const { toast } = await import("sonner");
        toast.error(translate("errors.rateLimit"));
      }
      throw error;
    }

    // 处理网络错误
    if (error instanceof TypeError && error.message === "Failed to fetch") {
      throw new ApiError(500, translate("errors.networkFailed"));
    }

    throw new ApiError(500, error instanceof Error ? error.message : translate("errors.unknown"));
  }
}

/**
 * GET 请求
 */
export function get<T = unknown>(
  endpoint: string,
  params?: Record<string, string | number | boolean>,
  options?: RequestOptions,
): Promise<T> {
  let url = endpoint;

  if (params) {
    const searchParams = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null) {
        searchParams.append(key, String(value));
      }
    });
    const queryString = searchParams.toString();
    if (queryString) {
      url += `?${queryString}`;
    }
  }

  return request<T>(url, {
    ...options,
    method: "GET",
  });
}

/**
 * POST 请求
 */
export function post<T = unknown>(
  endpoint: string,
  data?: unknown,
  options?: RequestOptions,
): Promise<T> {
  return request<T>(endpoint, {
    ...options,
    method: "POST",
    body: data instanceof FormData ? data : JSON.stringify(data),
  });
}

/**
 * PUT 请求
 */
export function put<T = unknown>(
  endpoint: string,
  data?: unknown,
  options?: RequestOptions,
): Promise<T> {
  return request<T>(endpoint, {
    ...options,
    method: "PUT",
    body: JSON.stringify(data),
  });
}

/**
 * PATCH 请求
 */
export function patch<T = unknown>(
  endpoint: string,
  data?: unknown,
  options?: RequestOptions,
): Promise<T> {
  return request<T>(endpoint, {
    ...options,
    method: "PATCH",
    body: JSON.stringify(data),
  });
}

/**
 * DELETE 请求
 */
export function del<T = unknown>(endpoint: string, options?: RequestOptions): Promise<T> {
  return request<T>(endpoint, {
    ...options,
    method: "DELETE",
  });
}

/**
 * 创建流式 SSE 连接（支持 POST 请求）
 *
 * @param endpoint  接口路径
 * @param data      请求体
 * @param options   请求选项，可通过 `signal` 传入外部 AbortSignal 以便调用方随时中止连接
 */
export async function createSSEStream(
  endpoint: string,
  data?: unknown,
  options?: RequestOptions,
): Promise<ReadableStream<Uint8Array>> {
  const url = endpoint.startsWith("http") ? endpoint : `${API_CONFIG.baseURL}${endpoint}`;

  const {
    timeout = API_CONFIG.timeout,
    headers = {},
    signal: externalSignal,
    skipAuthRefresh = false,
    skipAuthRedirect = false,
    ...fetchOptions
  } = options || {};
  delete fetchOptions.skipErrorHandler;

  const mergedHeaders: HeadersInit = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
    ...headers,
  };
  const csrfToken = readCsrfToken();
  if (csrfToken) {
    (mergedHeaders as Record<string, string>)["X-CSRF-Token"] = csrfToken;
  }
  const workspaceId = activeWorkspaceId();
  if (workspaceId) {
    (mergedHeaders as Record<string, string>)["X-Workspace-Id"] = workspaceId;
  }

  const controller = new AbortController();
  // 只对初始连接设置超时，连接建立后会清除
  const timeoutId = setTimeout(() => {
    controller.abort();
  }, timeout);

  // 将外部 AbortSignal 关联到内部 controller，
  // 这样调用方 abort 时会同时中止底层 fetch 连接
  if (externalSignal) {
    if (externalSignal.aborted) {
      clearTimeout(timeoutId);
      controller.abort();
    } else {
      externalSignal.addEventListener(
        "abort",
        () => {
          clearTimeout(timeoutId);
          controller.abort();
        },
        { once: true },
      );
    }
  }

  try {
    let response = await fetch(url, {
      ...fetchOptions,
      method: "POST",
      headers: mergedHeaders,
      body: JSON.stringify(data),
      signal: controller.signal,
      credentials: "include",
    });

    if (response.status === 401 && !skipAuthRefresh) {
      try {
        await refreshAuthOnce();
      } catch {
        if (!skipAuthRedirect) {
          dispatchAuthRequired();
        }
        await handleErrorResponse(response);
      }
      response = await fetch(url, {
        ...fetchOptions,
        method: "POST",
        headers: {
          ...mergedHeaders,
          ...buildAuthHeaders("POST", mergedHeaders, workspaceId),
        },
        body: JSON.stringify(data),
        signal: controller.signal,
        credentials: "include",
      });
    }

    // 连接已建立，清除初始连接的超时
    clearTimeout(timeoutId);

    if (!response.ok) {
      await handleErrorResponse(response);
    }

    if (!response.body) {
      throw new ApiError(500, translate("errors.emptyBody"));
    }

    return response.body;
  } catch (error) {
    clearTimeout(timeoutId);
    // 忽略 AbortError，这是正常的连接中止
    if (error instanceof Error && error.name === "AbortError") {
      throw error; // 重新抛出，让调用方处理
    }
    if (error instanceof ApiError) {
      throw error;
    }
    throw new ApiError(500, error instanceof Error ? error.message : translate("errors.unknown"));
  }
}

/**
 * 解析 SSE 事件流
 */
export async function parseSSEStream(
  stream: ReadableStream<Uint8Array>,
  onEvent: (event: MessageEvent) => void,
  onError?: (error: Error) => void,
  options: { propagateAbort?: boolean } = {},
): Promise<void> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();

      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      // A trailing CR may be the first half of a CRLF in the next chunk.
      const pendingCR = !done && buffer.endsWith("\r");
      if (pendingCR) buffer = buffer.slice(0, -1);
      buffer = buffer.replace(/\r\n/g, "\n").replace(/\r/g, "\n");

      const parts = buffer.split("\n\n");

      // 保留最后一个不完整的事件（可能没有以 \n\n 结尾）
      buffer = parts.pop() || "";

      // 处理完整的事件
      for (const part of parts) {
        if (part.trim()) {
          processSSEEvent(part, onEvent, onError);
        }
      }
      if (pendingCR) buffer += "\r";
      if (done) {
        if (buffer.trim()) processSSEBuffer(buffer, onEvent, onError);
        break;
      }
    }
  } catch (error) {
    // Legacy subscriptions deliberately consume cancellation; execution callers
    // need AbortError to distinguish cancelled work from a completed stream.
    if (error instanceof Error && error.name === "AbortError") {
      if (options.propagateAbort) throw error;
      return;
    }
    if (onError) {
      onError(error instanceof Error ? error : new Error(translate("errors.streamReadFailed")));
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // 忽略 releaseLock 错误，可能已经被释放
    }
  }
}

/**
 * 创建 ingest 事件的 SSE 流订阅（GET + EventSource 语义，支持断线重连的 event_id）
 *
 * @param path        完整资源路径（调用方拼接好资源 id 与 `/ingest`，不含 query string）
 * @param onEvent     收到事件时的回调
 * @param onError     出错时的回调
 * @param eventId     断线重连时的 Last-Event-Id，作为 `event_id` query 参数传递
 * @param onComplete  流正常结束时的回调
 * @returns           中止函数，调用后会中止底层连接
 */
export function createIngestStream(
  path: string,
  onEvent: SSEEventHandler,
  onError?: (error: Error) => void,
  eventId?: string,
  onComplete?: () => void,
): () => void {
  const controller = new AbortController();
  const url = `${path}${eventId ? `?event_id=${encodeURIComponent(eventId)}` : ""}`;

  const start = async () => {
    try {
      const response = await authenticatedFetch(url, {
        method: "GET",
        headers: { Accept: "text/event-stream" },
        signal: controller.signal,
      });
      if (!response.ok || !response.body) {
        throw new Error(
          translate("errors.ingestStreamConnectionFailed", { status: String(response.status) }),
        );
      }
      await parseSSEStream(
        response.body,
        (messageEvent) => {
          const data =
            typeof messageEvent.data === "string"
              ? JSON.parse(messageEvent.data)
              : messageEvent.data;
          onEvent({
            type: messageEvent.type as SSEEventData["type"],
            data,
          } as SSEEventData);
        },
        onError,
      );
      onComplete?.();
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        onError?.(err as Error);
      }
    }
  };
  void start();
  return () => controller.abort();
}

/**
 * 处理单个 SSE 事件
 */
function processSSEEvent(
  eventText: string,
  onEvent: (event: MessageEvent) => void,
  onError?: (error: Error) => void,
): void {
  let eventType = "message";
  let eventData = "";
  let eventId = "";

  const lines = eventText.split("\n");

  for (const line of lines) {
    if (line.startsWith("event:")) {
      eventType = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      // 支持多行数据
      const dataLine = line.slice(5);
      if (eventData) {
        eventData += "\n" + dataLine;
      } else {
        eventData = dataLine;
      }
    } else if (line.startsWith("id:")) {
      eventId = line.slice(3).trim();
    }
    // 忽略其他行（如 retry:、comment 等）
  }

  const normalizedEventData = eventData.trim();
  if (normalizedEventData) {
    try {
      const data = JSON.parse(normalizedEventData);
      onEvent(
        new MessageEvent(eventType, {
          data,
          lastEventId: eventId,
        }),
      );
    } catch (error) {
      if (onError) {
        onError(
          error instanceof Error
            ? error
            : new Error(translate("errors.sseParseFailed", { eventData })),
        );
      }
    }
  }
}

/**
 * 处理 SSE 缓冲区（用于处理流结束时的剩余数据）
 */
function processSSEBuffer(
  buffer: string,
  onEvent: (event: MessageEvent) => void,
  onError?: (error: Error) => void,
): void {
  const events = buffer.split("\n\n").filter((e) => e.trim());
  for (const event of events) {
    processSSEEvent(event, onEvent, onError);
  }
}

/** Authenticated persistent GET SSE. The id is opaque and is never parsed. */
export async function createAuthenticatedEventStream(
  endpoint: string,
  lastEventId?: string,
  options: RequestOptions = {},
): Promise<ReadableStream<Uint8Array>> {
  const headers = new Headers(options.headers);
  headers.set("Accept", "text/event-stream");
  if (lastEventId !== undefined) headers.set("Last-Event-ID", lastEventId);
  const response = await authenticatedFetch(endpoint, {
    ...options,
    method: "GET",
    headers: Object.fromEntries(headers.entries()),
  });
  if (!response.ok) await handleErrorResponse(response);
  if (!response.body) throw new ApiError(500, translate("errors.emptyBody"));
  return response.body;
}

/** Freeze scope across pagination; callers still guard user/generation ownership. */
export function snapshotRequestOptions(options: RequestOptions = {}): RequestOptions {
  return { ...options, workspaceId: options.workspaceId ?? activeWorkspaceId() };
}

/** Complete protected binary response, with common auth/scope/error handling. */
export async function getBlob(endpoint: string, options: RequestOptions = {}): Promise<Blob> {
  const response = await authenticatedFetch(endpoint, { ...options, method: "GET" });
  if (!response.ok) await handleErrorResponse(response);
  return response.blob();
}
