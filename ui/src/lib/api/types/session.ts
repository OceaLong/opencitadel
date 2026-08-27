import type { InferenceModel } from "../inference";
import type { SessionStatus, ToolEventStatus } from "./common";
import type { SkillSummary } from "./skills";

// ==================== 会话模块类型 ====================

/**
 * 会话信息
 */
export type Session = {
  session_id: string;
  title: string;
  latest_message: string;
  latest_message_at: string;
  status: SessionStatus;
  unread_message_count: number;
  mode?: SessionMode;
  resource_bindings?: SessionResourceBinding[];
  [key: string]: unknown;
};

/**
 * 会话列表响应
 */
export type SessionsData = {
  sessions: Session[];
};

/**
 * 创建会话请求参数
 */
export type SessionMode = "ask" | "agent";

export type CreateSessionParams = {
  title?: string;
  model_id?: string;
  skill_id?: string;
  thinking_enabled?: boolean;
  codebase_id?: string;
  codebase_version_id?: string;
  knowledge_base_id?: string;
  knowledge_base_version_id?: string;
  mode?: SessionMode;
  operator_scope?: "owned" | "third_party_saas";
  operator_domains?: string[];
  [key: string]: unknown;
};

export type ResourceKind = "knowledge_base" | "codebase";

export type SessionResourceBinding = {
  binding_id: string;
  resource_kind: ResourceKind;
  resource_id: string;
  version_id: string;
  is_current: boolean;
  supersedes_binding_id?: string | null;
};

export type ResourceBindingUpgrade = {
  old_binding_id: string;
  new_binding_id: string;
  current_version_id: string;
};

/**
 * 聊天消息
 */
export type ChatMessage = {
  role: "user" | "assistant" | "system";
  message: string;
  attachments?: Array<{
    file_id: string;
    filename: string;
    [key: string]: unknown;
  }>;
  /** Immutable turn snapshot; never derived from the current session pin. */
  resource_bindings?: SessionResourceBinding[];
  [key: string]: unknown;
};

type ChatCursor = {
  event_id?: string;
};

type ChatTurnParams = ChatCursor & {
  message: string;
  request_id: string;
  attachments?: string[];
  model_id?: string;
  skill_id?: string;
  thinking_enabled?: boolean;
  mode?: SessionMode;
};

type ChatResumeParams = ChatCursor & {
  message?: never;
  request_id?: never;
  attachments?: never;
  model_id?: never;
  skill_id?: never;
  thinking_enabled?: never;
  mode?: never;
};

/** A new turn is idempotent; a resume stream carries only its cursor. */
export type ChatParams = ChatTurnParams | ChatResumeParams;

/**
 * 会话详情（含事件列表，与 chat 流式响应格式一致）
 */
export type TokenUsageSummary = {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number;
  call_count: number;
};

export type TokenUsageRecord = {
  id: string;
  agent: string;
  step: string;
  model_id: string | null;
  model_name: string;
  call_type: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  created_at: string;
};

export type SessionTokenUsageData = {
  summary: TokenUsageSummary;
  records: TokenUsageRecord[];
};

export type SessionDetail = Session & {
  events?: SSEEventData[];
  events_next_cursor?: string | null;
  model_id?: string | null;
  skill_id?: string | null;
  thinking_enabled?: boolean;
  model?: InferenceModel | null;
  skill?: SkillSummary | null;
  token_usage?: TokenUsageSummary | null;
  mode?: SessionMode;
  operator_scope?: string | null;
  operator_domains?: string[];
  resource_bindings?: SessionResourceBinding[];
};

export type UpdateSessionConfigParams = {
  model_id?: string;
  skill_id?: string;
  thinking_enabled?: boolean;
  operator_domains?: string[];
};

/**
 * 工具调用事件
 */
export type ToolEvent = {
  tool_call_id?: string;
  name: string;
  function: string;
  args: Record<string, unknown>;
  content?: unknown;
  status?: ToolEventStatus;
  started_at?: number | string | null;
  ended_at?: number | string | null;
  duration_ms?: number | null;
  error?: string | null;
  span_id?: string | null;
  parent_span_id?: string | null;
  [key: string]: unknown;
};

/**
 * SSE 事件类型
 */
export type EventVisibility = "user";
type EventChannel = "ui";

export type EventMeta = {
  event_id?: string;
  schema_version: number;
  visibility: EventVisibility;
  channel: EventChannel;
  persist: boolean;
  created_at: number;
};

export type SSEEventType =
  | "message"
  | "session_status"
  | "tool"
  | "done"
  | "error"
  | "approval"
  | "resource_build";

/**
 * SSE 事件数据
 */
export type SSEEventData =
  | { type: "message"; data: ChatMessage & EventMeta }
  | {
      type: "session_status";
      data: {
        status: SessionStatus;
        reason?: string | null;
        code?: string | null;
      } & EventMeta;
    }
  | { type: "tool"; data: ToolEvent & EventMeta }
  | { type: "done"; data: Record<string, unknown> & EventMeta }
  | {
      type: "error";
      data: {
        error: string;
        code?: string | null;
        incident_id?: string | null;
        retryable?: boolean | null;
      } & EventMeta;
    }
  | {
      type: "approval";
      data: {
        approval_id: string;
        kind: "tool";
        payload: Record<string, unknown>;
        options: Array<"approve" | "reject">;
      } & EventMeta;
    }
  | {
      type: "resource_build";
      data: {
        activity_id: string;
        kind: string;
        phase?: string | null;
        status?: string | null;
        progress: number;
        message: string;
      } & EventMeta;
    };

/**
 * SSE 事件处理器
 */
export type SSEEventHandler = (event: SSEEventData) => void;

export type SessionEventsPage = {
  events: SSEEventData[];
  next_cursor?: string | null;
  prev_cursor?: string | null;
  has_earlier?: boolean;
};
