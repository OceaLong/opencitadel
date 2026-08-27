import { del, get, post, put } from "./fetch";
import type { MemoryEntriesData, MemoryEntry, MemoryScope } from "./types";

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
    }>,
  ): Promise<MemoryEntry> => put<MemoryEntry>(`/memories/${id}`, entry),

  delete: (id: string): Promise<void> => del<void>(`/memories/${id}`),
};
