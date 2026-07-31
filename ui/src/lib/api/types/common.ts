/**
 * API 统一响应格式
 */
export type ApiResponse<T = unknown> = {
  code: number;
  msg: string;
  data: T | null;
  error_key?: string | null;
  error_params?: Record<string, string> | null;
  i18n_key?: string | null;
  i18n_params?: Record<string, string> | null;
};

/**
 * 会话状态
 */
export type SessionStatus =
  | "pending"
  | "running"
  | "waiting"
  | "completed"
  | "cancelled"
  | "failed";

/**
 * 执行状态
 */
export type ExecutionStatus = "pending" | "running" | "completed" | "failed";

/**
 * 工具事件状态
 */
export type ToolEventStatus = "calling" | "called" | "error";

/**
 * MCP 传输类型
 */
export type MCPTransport = "stdio" | "sse" | "streamable_http";
