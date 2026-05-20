import { get, post, put, del } from "./fetch";
import type {
  MemoryEntry,
  MemoryEntriesData,
  MemoryScope,
  SessionMemoryData,
} from "./types";

export const memoryApi = {
  list: (params?: {
    scope?: MemoryScope;
    session_id?: string;
    q?: string;
    tags?: string;
  }): Promise<MemoryEntriesData> => {
    const query = new URLSearchParams();
    if (params?.scope) query.set("scope", params.scope);
    if (params?.session_id) query.set("session_id", params.session_id);
    if (params?.q) query.set("q", params.q);
    if (params?.tags) query.set("tags", params.tags);
    const qs = query.toString();
    return get<MemoryEntriesData>(`/memories${qs ? `?${qs}` : ""}`);
  },

  get: (id: string): Promise<MemoryEntry> => get<MemoryEntry>(`/memories/${id}`),

  create: (entry: {
    title: string;
    content: string;
    tags?: string[];
    scope?: MemoryScope;
    session_id?: string;
  }): Promise<MemoryEntry> => post<MemoryEntry>("/memories", entry),

  update: (
    id: string,
    entry: Partial<{
      title: string;
      content: string;
      tags: string[];
      scope: MemoryScope;
      session_id: string;
    }>
  ): Promise<MemoryEntry> => put<MemoryEntry>(`/memories/${id}`, entry),

  delete: (id: string): Promise<void> => del<void>(`/memories/${id}`),

  getSessionMemory: (sessionId: string): Promise<SessionMemoryData> =>
    get<SessionMemoryData>(`/sessions/${sessionId}/memory`),

  compactSessionMemory: (sessionId: string, agentName: string): Promise<void> =>
    post<void>(`/sessions/${sessionId}/memory/compact`, { agent_name: agentName }),

  clearSessionMemory: (sessionId: string, agentName: string): Promise<void> =>
    post<void>(`/sessions/${sessionId}/memory/clear`, { agent_name: agentName }),

  deleteSessionMemoryMessage: (
    sessionId: string,
    agentName: string,
    index: number
  ): Promise<void> =>
    del<void>(`/sessions/${sessionId}/memory/${agentName}/messages/${index}`),
};
