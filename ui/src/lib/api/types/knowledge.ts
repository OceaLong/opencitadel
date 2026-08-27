import type { ExecutionRunStatus } from "./execution";
import type { SessionMode } from "./session";

// ==================== 文档知识库 ====================

type KnowledgeBaseStatus =
  | "pending"
  | "parsing"
  | "chunking"
  | "indexing"
  | "graph_building"
  | "ready"
  | "failed";

type KnowledgeDocumentStatus = "pending" | "parsing" | "ready" | "failed";
export type KnowledgeSourceType = "upload" | "zip" | "web" | "confluence" | "feishu";

export type KnowledgeBase = {
  id: string;
  name: string;
  status: KnowledgeBaseStatus;
  doc_count: number;
  chunk_count: number;
  ready_doc_count?: number;
  active_version_id?: string | null;
  error?: string | null;
  vector_degraded?: boolean;
  settings?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
};

export type KnowledgeBasesData = {
  knowledge_bases: KnowledgeBase[];
};

export type KnowledgeDocument = {
  id: string;
  kb_id: string;
  title: string;
  source_type: KnowledgeSourceType;
  mime: string;
  file_id?: string | null;
  page_count: number;
  status: KnowledgeDocumentStatus;
  error?: string | null;
  warning?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type KnowledgeDocumentsData = {
  documents: KnowledgeDocument[];
  total: number;
};

export type CreateKnowledgeBaseParams = {
  name?: string;
  settings?: Record<string, unknown>;
};

export type AddKnowledgeDocumentsParams = {
  file_ids?: string[];
  urls?: string[];
  source_type?: KnowledgeSourceType;
};

export type CreateKnowledgeSessionParams = {
  mode?: SessionMode;
  model_id?: string;
  skill_id?: string;
  knowledge_base_version_id?: string;
};

export type KnowledgeSessionData = {
  session_id: string;
  knowledge_base_id: string;
  mode: SessionMode;
};

export type ReadKnowledgeDocumentData = {
  document: KnowledgeDocument;
  version_id: string;
  document_revision_id: string;
  items: KnowledgeDocumentContentItem[];
  next_cursor?: string | null;
  total: number;
  truncated: boolean;
};

type KnowledgeVersionState = "building" | "ready" | "degraded" | "failed";

export type KnowledgeBuild = {
  id: string;
  run_id?: string | null;
  knowledge_base_id: string;
  version_id: string;
  status: ExecutionRunStatus;
  phase?: string | null;
  progress: number;
  failure_code?: string | null;
  created_at: string;
  updated_at: string;
  terminal_at?: string | null;
  can_retry: boolean;
  can_cancel: boolean;
};

export type KnowledgeVersion = {
  id: string;
  knowledge_base_id: string;
  parent_version_id?: string | null;
  build_id: string;
  state: KnowledgeVersionState;
  capabilities: Record<string, unknown>;
  degraded_reasons: string[];
  metrics: Record<string, unknown>;
  created_at: string;
  published_at?: string | null;
  is_active: boolean;
  is_published: boolean;
  is_candidate: boolean;
  build?: KnowledgeBuild | null;
};

export type KnowledgeVersionsData = {
  knowledge_base_id: string;
  active_version_id?: string | null;
  active_build?: KnowledgeBuild | null;
  versions: KnowledgeVersion[];
};

type KnowledgeCitation = {
  version_id: string;
  document_revision_id: string;
  doc_id: string;
  page_no?: number | null;
  chunk_id?: string | null;
};

type KnowledgeGraphNode = {
  id: string;
  name: string;
  type: string;
  description: string;
};

type KnowledgeGraphEdge = {
  id: string;
  source: string;
  target: string;
  relation: string;
  evidence: KnowledgeCitation[];
};

export type KnowledgeGraphData = {
  nodes: KnowledgeGraphNode[];
  edges: KnowledgeGraphEdge[];
  capability: boolean;
  next_cursor?: string | null;
};

export type KnowledgeDocumentContentItem = {
  id: string;
  page_no?: number | null;
  heading_path: string;
  ordinal: number;
  content: string;
};
