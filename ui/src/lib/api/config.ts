import { del, get, post, put } from "./fetch";
import type {
  A2AServersData,
  AgentConfig,
  AppConfigRevision,
  CreateA2AServerParams,
  MCPConfig,
  MCPServersData,
} from "./types";

export type AppConfigSection =
  | "server"
  | "agent_config"
  | "memory"
  | "sandbox"
  | "worker"
  | "streams"
  | "observability"
  | "model_resilience"
  | "feature_flags"
  | "hitl"
  | "scheduler"
  | "knowledge_base";

/**
 * 配置模块 API
 */
export const configApi = {
  listSections: (): Promise<string[]> => get<string[]>("/app-config/sections"),

  getSection: <T extends Record<string, unknown>>(
    section: AppConfigSection,
    useUserOverride = false,
  ): Promise<T> =>
    get<T>(
      `/app-config/sections/${section}`,
      useUserOverride ? { use_user_override: "true" } : undefined,
    ),

  updateSection: <T extends Record<string, unknown>>(
    section: AppConfigSection,
    payload: T,
  ): Promise<T> => put<T>(`/app-config/sections/${section}`, payload),

  deleteUserOverride: (): Promise<void> => del<void>("/app-config/user-override"),

  listRevisions: (params?: {
    scope?: string;
    owner_user_id?: string;
    limit?: number;
    offset?: number;
  }): Promise<AppConfigRevision[]> => get<AppConfigRevision[]>("/app-config/revisions", params),

  rollbackRevision: (revisionId: string): Promise<Record<string, unknown>> =>
    post<Record<string, unknown>>(`/app-config/revisions/${revisionId}/rollback`, {}),

  getAgentConfig: (): Promise<AgentConfig> => get<AgentConfig>("/app-config/agent"),

  updateAgentConfig: (config: AgentConfig): Promise<AgentConfig> =>
    post<AgentConfig>("/app-config/agent", config),

  getMCPServers: (): Promise<MCPServersData> => get<MCPServersData>("/app-config/mcp-servers"),

  addMCPServer: (config: MCPConfig): Promise<void> => post<void>("/app-config/mcp-servers", config),

  deleteMCPServer: (serverName: string): Promise<void> =>
    post<void>(`/app-config/mcp-servers/${serverName}/delete`, {}),

  updateMCPServerEnabled: (serverName: string, enabled: boolean): Promise<void> =>
    post<void>(`/app-config/mcp-servers/${serverName}/enabled`, { enabled }),

  getA2AServers: (): Promise<A2AServersData> => get<A2AServersData>("/app-config/a2a-servers"),

  addA2AServer: (params: CreateA2AServerParams): Promise<void> =>
    post<void>("/app-config/a2a-servers", params),

  deleteA2AServer: (a2aId: string): Promise<void> =>
    post<void>(`/app-config/a2a-servers/${a2aId}/delete`, {}),

  updateA2AServerEnabled: (a2aId: string, enabled: boolean): Promise<void> =>
    post<void>(`/app-config/a2a-servers/${a2aId}/enabled`, { enabled }),
};
