import type { SessionMode } from "./session";

// ==================== 代码知识库 ====================

export type CodebaseStatus =
  | "pending"
  | "materializing"
  | "analyzing"
  | "indexing"
  | "generating"
  | "ready"
  | "failed";

export type CodebaseSourceType = "zip" | "git" | "files";

type ArtifactKind =
  | "architecture"
  | "data_flow"
  | "module_dir"
  | "flowchart"
  | "call_chain"
  | "overview";

export type Codebase = {
  id: string;
  name: string;
  source_type: CodebaseSourceType;
  source_ref?: string;
  status: CodebaseStatus;
  language_stats?: Record<string, number>;
  file_count?: number;
  sandbox_id?: string | null;
  workspace_path?: string;
  ingest_task_id?: string | null;
  error?: string | null;
  vector_degraded?: boolean;
  active_version_id?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type CodebasesData = {
  codebases: Codebase[];
};

export type FileTreeNode = {
  name: string;
  path: string;
  is_dir: boolean;
  language?: string;
  children?: FileTreeNode[];
};

export type FileTreeData = {
  tree: FileTreeNode[];
};

export type CodebaseSymbol = {
  id: string;
  name: string;
  kind: string;
  file_id: string;
  path?: string;
  signature?: string;
  start_line?: number;
  end_line?: number;
  parent_id?: string | null;
};

export type CodebaseSymbolsData = {
  symbols: CodebaseSymbol[];
};

export type CodebaseArtifact = {
  id: string;
  version_id?: string | null;
  kind: ArtifactKind;
  format: "mermaid" | "markdown";
  title: string;
  content: string;
  meta?: Record<string, unknown>;
  created_at?: string;
};

export type CodebaseArtifactsData = {
  artifacts: CodebaseArtifact[];
};

export type CreateCodebaseParams = {
  name?: string;
  source_type: CodebaseSourceType;
  file_id?: string;
  git_url?: string;
  file_ids?: string[];
};

export type CreateCodebaseSessionParams = {
  mode?: SessionMode;
  model_id?: string;
  skill_id?: string;
  codebase_version_id?: string;
};

export type CodebaseSessionData = {
  session_id: string;
  codebase_id: string;
  mode: SessionMode;
};

export type ReadSourceParams = {
  path: string;
  start_line?: number;
  end_line?: number;
};

export type ReadSourceData = {
  path: string;
  content: string;
  start_line?: number;
  end_line?: number;
};

export type DownloadCodebaseData = {
  snapshot_key: string;
  download_url?: string;
};

type CodebaseBuildState =
  | "queued"
  | "running"
  | "succeeded"
  | "degraded"
  | "failed"
  | "cancelled";

type CodebaseVersionState = "building" | "ready" | "degraded" | "failed";

export type CodebaseBuild = {
  id: string;
  codebase_id: string;
  version_id: string;
  parent_version_id?: string | null;
  command_key: string;
  state: CodebaseBuildState;
  phase?: string | null;
  progress: number;
  capabilities: unknown[];
  degraded_reasons: unknown[];
  metrics: Record<string, unknown>;
  error_code?: string | null;
  error_message?: string | null;
  heartbeat_at?: string | null;
  last_event_seq: number;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  can_retry: boolean;
  can_cancel: boolean;
};

export type CodebaseVersion = {
  id: string;
  codebase_id: string;
  parent_version_id?: string | null;
  build_id?: string | null;
  state: CodebaseVersionState;
  source_snapshot_key?: string | null;
  source_revision?: string;
  source_digest?: string;
  capabilities: Record<string, unknown>;
  degraded_reasons: string[];
  metrics: Record<string, unknown>;
  created_at: string;
  published_at?: string | null;
  is_active: boolean;
  is_published: boolean;
  is_candidate: boolean;
  build?: CodebaseBuild | null;
};

export type CodebaseVersionsData = {
  codebase_id: string;
  active_version_id?: string | null;
  active_build?: CodebaseBuild | null;
  versions: CodebaseVersion[];
};
