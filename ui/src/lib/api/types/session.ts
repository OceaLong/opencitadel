import type { ExecutionStatus, SessionStatus, ToolEventStatus } from "./common";
import type { LLMModel } from "./models";
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
  codebase_id?: string | null;
  knowledge_base_id?: string | null;
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
  gate_profile?: "loose" | "standard" | "strict";
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
 * 结构化澄清回答
 */
export type ClarifyAnswer = {
  question_id: string;
  prompt?: string;
  option_ids: string[];
  option_labels: string[];
  custom_text?: string;
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
  clarify_answers?: ClarifyAnswer[];
  /** Immutable turn snapshot; never derived from the current session pin. */
  resource_bindings?: SessionResourceBinding[];
  [key: string]: unknown;
};

/**
 * 聊天请求参数
 * message 为空时用于流式拉取未完成任务的事件列表
 */
export type ChatParams = {
  message?: string;
  attachments?: string[];
  clarify_answers?: ClarifyAnswer[];
  event_id?: string;
  model_id?: string;
  skill_id?: string;
  thinking_enabled?: boolean;
  mode?: SessionMode;
  [key: string]: unknown;
};

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
  events_next_cursor?: number | null;
  model_id?: string | null;
  skill_id?: string | null;
  thinking_enabled?: boolean;
  model?: LLMModel | null;
  skill?: SkillSummary | null;
  token_usage?: TokenUsageSummary | null;
  codebase_id?: string | null;
  knowledge_base_id?: string | null;
  mode?: SessionMode;
  operator_scope?: string | null;
  operator_domains?: string[];
  gate_profile?: string | null;
  awaiting_human?: boolean;
  resource_bindings?: SessionResourceBinding[];
};

export type UpdateSessionConfigParams = {
  model_id?: string;
  skill_id?: string;
  thinking_enabled?: boolean;
  gate_profile?: string;
  operator_domains?: string[];
};

/**
 * 计划步骤
 */
export type PlanStep = {
  id: string;
  description: string;
  status: ExecutionStatus;
  [key: string]: unknown;
};

/**
 * 计划事件
 */
export type PlanEvent = {
  steps: PlanStep[];
  [key: string]: unknown;
};

/**
 * 步骤事件
 */
export type StepEvent = {
  id: string;
  status: ExecutionStatus;
  description: string;
  started_at?: number | string | null;
  ended_at?: number | string | null;
  duration_ms?: number | null;
  error?: string | null;
  span_id?: string | null;
  parent_span_id?: string | null;
  [key: string]: unknown;
};

/** 子 Agent 委派事件 */
export type SubAgentEvent = {
  subagent_id: string;
  goal: string;
  status: "started" | "completed" | "failed";
  result_preview?: string | null;
  error?: string | null;
  [key: string]: unknown;
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

type ClarifyOption = {
  id: string;
  label: string;
};

export type ClarifyQuestion = {
  id: string;
  prompt: string;
  options: ClarifyOption[];
  allow_multiple?: boolean;
  allow_custom?: boolean;
};

/**
 * SSE 事件类型
 */
export type EventVisibility = "user" | "internal" | "debug";
type EventChannel = "ui" | "debug" | "runtime";

export type EventMeta = {
  event_id?: string;
  schema_version: number;
  visibility: EventVisibility;
  channel: EventChannel;
  persist: boolean;
  created_at: number;
};

export type SSEEventType =
  | "clarify"
  | "message"
  | "message_delta"
  | "reasoning_delta"
  | "tool_args_delta"
  | "assistant_notice"
  | "session_status"
  | "debug_item"
  | "title"
  | "plan"
  | "step"
  | "tool"
  | "wait"
  | "usage"
  | "done"
  | "error"
  | "artifact"
  | "approval";

/**
 * SSE 事件数据
 */
export type DebugItemEvent = {
  item_type: string;
  payload: Record<string, unknown>;
} & EventMeta;

export type SSEEventData =
  | {
      type: "clarify";
      data: { title?: string | null; questions: ClarifyQuestion[] } & EventMeta;
    }
  | { type: "message"; data: ChatMessage & EventMeta }
  | { type: "message_delta"; data: { stream_id: string; delta: string; role?: string } & EventMeta }
  | { type: "reasoning_delta"; data: { stream_id: string; delta: string } & EventMeta }
  | {
      type: "tool_args_delta";
      data: {
        stream_id: string;
        tool_call_id: string;
        tool_name?: string;
        delta: string;
      } & EventMeta;
    }
  | {
      type: "assistant_notice";
      data: {
        message: string;
        i18n_key?: string;
        i18n_params?: Record<string, string | number>;
      } & EventMeta;
    }
  | {
      type: "session_status";
      data: {
        status: SessionStatus;
        run_epoch_id?: string | null;
        reason?: string | null;
        code?: string | null;
      } & EventMeta;
    }
  | { type: "debug_item"; data: DebugItemEvent }
  | { type: "title"; data: { title: string } & EventMeta }
  | { type: "plan"; data: PlanEvent & EventMeta }
  | { type: "step"; data: StepEvent & EventMeta }
  | { type: "subagent"; data: SubAgentEvent & EventMeta }
  | { type: "tool"; data: ToolEvent & EventMeta }
  | { type: "wait"; data: Record<string, unknown> & EventMeta }
  | {
      type: "usage";
      data: TokenUsageSummary & {
        delta_prompt_tokens?: number;
        delta_completion_tokens?: number;
      } & EventMeta;
    }
  | { type: "done"; data: Record<string, unknown> & EventMeta }
  | { type: "error"; data: { error: string; code?: string | null } & EventMeta }
  | {
      type: "artifact";
      data: {
        artifact_id: string;
        kind: "doc" | "web";
        title: string;
        status: "draft" | "updated" | "final";
        storage_ref: string;
        version: number;
      } & EventMeta;
    }
  | {
      type: "approval";
      data: {
        approval_id: string;
        kind: "plan" | "tool" | "takeover";
        payload: Record<string, unknown>;
        options: string[];
      } & EventMeta;
    };

/**
 * SSE 事件处理器
 */
export type SSEEventHandler = (event: SSEEventData) => void;

export type SessionEventsPage = {
  events: SSEEventData[];
  next_cursor?: number | null;
  prev_cursor?: number | null;
  has_earlier?: boolean;
};

type CheckpointAnchorType = "user_message" | "step";

export type SessionCheckpoint = {
  id: string;
  session_id: string;
  anchor_type: CheckpointAnchorType;
  anchor_event_id: string;
  label: string;
  created_at: string;
};
