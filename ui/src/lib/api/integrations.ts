import { del, get, patch, post, put } from "./fetch";
import type { components } from "./generated/schema";

export type MCPServer = components["schemas"]["MCPServerResponse"];
export type MCPServerList = components["schemas"]["MCPServerListResponse"];
export type CreateMCPServerRequest = components["schemas"]["CreateMCPServerRequest"];
export type UpdateMCPServerRequest = components["schemas"]["UpdateMCPServerRequest"];
export type A2AServer = components["schemas"]["A2AServerResponse"];
export type A2AServerList = components["schemas"]["A2AServerListResponse"];
export type CreateA2AServerRequest = components["schemas"]["CreateA2AServerRequest"];
export type UpdateA2AServerRequest = components["schemas"]["UpdateA2AServerRequest"];
export type SetIntegrationEnabledRequest = components["schemas"]["SetIntegrationEnabledRequest"];
export type MCPTransport = components["schemas"]["MCPTransport"];

export const integrationsApi = {
  listMCPServers: (): Promise<MCPServerList> => get<MCPServerList>("/integrations/mcp-servers"),
  createMCPServer: (body: CreateMCPServerRequest): Promise<MCPServer> =>
    post<MCPServer>("/integrations/mcp-servers", body),
  updateMCPServer: (serverId: string, body: UpdateMCPServerRequest): Promise<MCPServer> =>
    put<MCPServer>(`/integrations/mcp-servers/${encodeURIComponent(serverId)}`, body),
  deleteMCPServer: (serverId: string): Promise<void> =>
    del<void>(`/integrations/mcp-servers/${encodeURIComponent(serverId)}`),
  setMCPServerEnabled: (serverId: string, body: SetIntegrationEnabledRequest): Promise<MCPServer> =>
    patch<MCPServer>(`/integrations/mcp-servers/${encodeURIComponent(serverId)}/enabled`, body),
  listA2AServers: (): Promise<A2AServerList> => get<A2AServerList>("/integrations/a2a-servers"),
  createA2AServer: (body: CreateA2AServerRequest): Promise<A2AServer> =>
    post<A2AServer>("/integrations/a2a-servers", body),
  updateA2AServer: (serverId: string, body: UpdateA2AServerRequest): Promise<A2AServer> =>
    put<A2AServer>(`/integrations/a2a-servers/${encodeURIComponent(serverId)}`, body),
  deleteA2AServer: (serverId: string): Promise<void> =>
    del<void>(`/integrations/a2a-servers/${encodeURIComponent(serverId)}`),
  setA2AServerEnabled: (serverId: string, body: SetIntegrationEnabledRequest): Promise<A2AServer> =>
    patch<A2AServer>(`/integrations/a2a-servers/${encodeURIComponent(serverId)}/enabled`, body),
};
