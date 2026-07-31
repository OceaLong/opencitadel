import type { MCPTransport } from "./common";

// ==================== 配置模块类型 ====================

/**
 * Agent 通用配置
 */
export type AgentConfig = {
  max_iterations?: number;
  max_retries?: number;
  max_search_results?: number;
  [key: string]: unknown;
};

export type AppConfigRevision = {
  id: string;
  config_id: string;
  scope: string;
  owner_user_id?: string | null;
  payload: Record<string, unknown>;
  changed_by?: string | null;
  note?: string;
  created_at: string;
};

/**
 * MCP 服务器列表项（GET 响应）
 */
export type ListMCPServerItem = {
  server_name: string;
  server_id?: string;
  enabled: boolean;
  transport: MCPTransport;
  tools: string[];
  connection_status?: "disabled" | "connected" | "error" | "pending";
  connection_error?: string | null;
  config?: MCPServerConfig | null;
};

/**
 * MCP 服务器列表响应
 */
export type MCPServersData = {
  mcp_servers: ListMCPServerItem[];
};

/**
 * MCP 服务器配置（POST 请求体中单个服务器的配置）
 */
export type MCPServerConfig = {
  transport?: MCPTransport;
  enabled?: boolean;
  description?: string | null;
  env?: Record<string, unknown> | null;
  command?: string | null;
  args?: string[] | null;
  url?: string | null;
  headers?: Record<string, unknown> | null;
  [key: string]: unknown;
};

/**
 * MCP 配置（POST 新增 MCP 服务的请求体）
 */
export type MCPConfig = {
  mcpServers: Record<string, MCPServerConfig>;
  [key: string]: unknown;
};

/**
 * A2A 服务器列表项（GET 响应）
 */
export type ListA2AServerItem = {
  id: string;
  name: string;
  description: string;
  input_modes: string[];
  output_modes: string[];
  streaming: boolean;
  push_notifications: boolean;
  enabled: boolean;
};

/**
 * A2A 服务器列表响应
 */
export type A2AServersData = {
  a2a_servers: ListA2AServerItem[];
};

/**
 * 新增 A2A 服务器请求参数
 */
export type CreateA2AServerParams = {
  base_url: string;
};
