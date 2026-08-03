// ==================== 记忆管理 ====================

export type MemoryScope = "global" | "session";
type MemorySource = "manual" | "auto_extracted" | "tool_save";

export type MemoryEntry = {
  id: string;
  scope: MemoryScope;
  session_id?: string | null;
  title: string;
  content: string;
  tags: string[];
  source: MemorySource;
  last_used_at?: string | null;
  use_count: number;
  created_at?: string;
  updated_at?: string;
};

export type MemoryEntriesData = {
  entries: MemoryEntry[];
};

export type SessionMemoryData = {
  planner: Array<Record<string, unknown>>;
  react: Array<Record<string, unknown>>;
};
