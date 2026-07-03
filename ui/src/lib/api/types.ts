/**
 * API 统一响应格式
 */
export type ApiResponse<T = unknown> = {
  code: number;
  msg: string;
  data: T | null;
};

/**
 * 会话状态
 */
export type SessionStatus =
  | "pending"
  | "running"
  | "waiting"
  | "completed"
  | "cancelled"
  | "failed";

/**
 * 执行状态
 */
export type ExecutionStatus = "pending" | "running" | "completed" | "failed";

/**
 * 工具事件状态
 */
export type ToolEventStatus = "calling" | "called" | "error";

/**
 * MCP 传输类型
 */
export type MCPTransport = "stdio" | "sse" | "streamable_http";

// ==================== 模型管理 ====================

export type LLMProvider = "openai" | "anthropic" | "gemini" | "ollama" | "azure";

export type ModelCapabilities = {
  vision: boolean;
  vision_with_tools?: boolean;
  max_image_bytes?: number;
  max_images_per_request?: number;
  image_encoding?: "data_url" | "url";
};

export type LLMModel = {
  id: string;
  endpoint_id: string;
  display_name: string;
  provider: LLMProvider;
  base_url: string;
  api_key?: string;
  model_name: string;
  temperature: number;
  max_tokens: number;
  extra_params?: Record<string, unknown>;
  capabilities?: ModelCapabilities;
  supports_multimodal?: boolean;
  is_default: boolean;
  visibility?: "global" | "private";
  owner_user_id?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type LLMModelsData = {
  models: LLMModel[];
};

export type CreateLLMModelParams = {
  endpoint_id: string;
  display_name: string;
  model_name: string;
  temperature?: number;
  max_tokens?: number;
  extra_params?: Record<string, unknown>;
  capabilities?: ModelCapabilities;
  supports_multimodal?: boolean;
  is_default?: boolean;
};

export type LLMEndpointModelSummary = {
  id: string;
  display_name: string;
  model_name: string;
  is_default: boolean;
};

export type LLMEndpoint = {
  id: string;
  display_name: string;
  provider: LLMProvider;
  base_url: string;
  api_key?: string;
  visibility?: "global" | "private";
  owner_user_id?: string | null;
  model_count?: number;
  models?: LLMEndpointModelSummary[];
  created_at?: string;
  updated_at?: string;
};

export type LLMEndpointsData = {
  endpoints: LLMEndpoint[];
};

export type CreateLLMEndpointParams = {
  display_name: string;
  provider: LLMProvider;
  base_url: string;
  api_key?: string;
};

export type MultimodalProbeResult = {
  status: string;
  message?: string;
  error_code?: string | null;
};

// ==================== Skill 管理 ====================

export type SkillAgentParams = {
  max_iterations?: number;
  max_retries?: number;
  max_search_results?: number;
  temperature_override?: number;
};

export type Skill = {
  id: string;
  name: string;
  slug: string;
  description: string;
  icon: string;
  category: string;
  system_prompt: string;
  allowed_tools: string[];
  mcp_server_refs?: string[];
  a2a_server_refs?: string[];
  recommended_model_id?: string | null;
  agent_params: SkillAgentParams;
  examples: string[];
  is_builtin: boolean;
  enabled: boolean;
  visibility?: "global" | "private";
  owner_user_id?: string | null;
  created_at?: string;
  updated_at?: string;
};

export type SkillsData = {
  skills: Skill[];
};

export type SkillSummary = {
  id: string;
  name: string;
  icon: string;
  examples: string[];
};

export type CreateSkillParams = {
  name: string;
  slug?: string;
  description?: string;
  icon?: string;
  category?: string;
  system_prompt?: string;
  allowed_tools?: string[];
  mcp_server_refs?: string[];
  a2a_server_refs?: string[];
  recommended_model_id?: string | null;
  agent_params?: SkillAgentParams;
  examples?: string[];
  enabled?: boolean;
};

// ==================== 记忆管理 ====================

export type MemoryScope = "global" | "session";
export type MemorySource = "manual" | "auto_extracted" | "tool_save";

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

// ==================== 应用市场 ====================

export type ModelDependency = "none" | "optional" | "required";

export type MarketplaceApp = {
  id: string;
  name: string;
  description: string;
  icon: string;
  category: string;
  tags: string[];
  featured: boolean;
  accent: string;
  needs_vision: boolean;
  model_dependency?: ModelDependency;
  examples: string[];
};

export type LLMStatusData = {
  status: "not_configured" | "configured" | "ok" | "degraded" | "unknown";
  default_model?: {
    model_id: string;
    display_name: string;
    provider: string;
    base_url_configured: boolean;
    api_key_configured: boolean;
  } | null;
  embedding: {
    api_key_configured: boolean;
    vector_enabled: boolean;
    enabled: boolean;
  };
};

export type MarketplaceAppsData = {
  apps: MarketplaceApp[];
};

// ==================== 问卷 ====================

export type QuestionType = "single" | "multiple" | "rating" | "text";

export type QuestionOption = {
  id: string;
  text: string;
};

export type QuestionItem = {
  id: string;
  type: QuestionType;
  text: string;
  options?: QuestionOption[];
  required?: boolean;
  rating_max?: number;
};

export type QuestionnaireData = {
  id: string;
  title: string;
  description: string;
  questions: QuestionItem[];
  status: "draft" | "published" | "closed";
  slug: string;
  manage_token?: string;
  created_at?: string;
  updated_at?: string;
};

export type CreateQuestionnaireParams = {
  title: string;
  description?: string;
  questions?: QuestionItem[];
};

export type UpdateQuestionnaireParams = {
  manage_token: string;
  title?: string;
  description?: string;
  questions?: QuestionItem[];
};

export type PublishQuestionnaireParams = {
  manage_token: string;
};

export type SubmitQuestionnaireResponseParams = {
  answers: Record<string, unknown>;
  respondent_name?: string;
};

export type SubmitQuestionnaireResponseResult = {
  id: string;
  message: string;
};

export type QuestionStats = {
  text: string;
  type: string;
  counts?: Record<string, number>;
  labels?: Record<string, string>;
  average?: number;
  count?: number;
  distribution?: Record<string, number>;
  responses?: Array<{ text: string; name?: string | null }>;
};

export type QuestionnaireStatsData = {
  questionnaire_id: string;
  title: string;
  status: string;
  slug: string;
  total_responses: number;
  per_question: Record<string, QuestionStats>;
};

// ==================== 房间 ====================

export type RoomParticipant = {
  id: string;
  name: string;
  joined_at?: string;
  last_seen?: string;
  online?: boolean;
};

export type RoomEvent = {
  id: string;
  type: string;
  payload: Record<string, unknown>;
  created_at?: string;
};

export type RoomData = {
  id: string;
  code: string;
  name: string;
  host_participant_id: string;
  tod_mode: "random" | "custom";
  turn_order: string[];
  current_turn_index: number;
  current_turn_id?: string | null;
  current_turn_name?: string | null;
  status: string;
  participants: RoomParticipant[];
  recent_events?: RoomEvent[];
};

export type CreateRoomParams = {
  name: string;
  host_name: string;
  tod_mode?: "random" | "custom";
};

export type CreateRoomResult = {
  room: RoomData;
  participant_id: string;
};

export type JoinRoomParams = {
  name: string;
};

export type JoinRoomResult = {
  room: RoomData;
  participant_id: string;
};

export type RollDiceParams = {
  participant_id: string;
  dice_count?: number;
  dice_faces?: number;
};

export type DrawTodParams = {
  participant_id: string;
  category?: "truth" | "dare";
};

export type NextTurnParams = {
  participant_id: string;
};

export type AddTodPromptParams = {
  participant_id: string;
  category: "truth" | "dare";
  text: string;
};

export type NutritionAnalysisParams = {
  file_id: string;
  model_id?: string;
  weight_kg?: number;
  goal?: "cut" | "bulk" | "maintain";
};

export type NutritionItem = {
  name: string;
  grams: number;
  confidence: number;
  calories: number;
  protein: number;
  fat: number;
  carbs: number;
};

export type NutritionAnalysisData = {
  meal_summary: string;
  items: NutritionItem[];
  totals: {
    calories: number;
    protein: number;
    fat: number;
    carbs: number;
  };
  assessment: {
    overall: "green" | "yellow" | "red";
    lights: Record<string, "green" | "yellow" | "red">;
    tips: string[];
    goal?: string | null;
    ratios: {
      calories_per_kg?: number | null;
      protein_per_kg?: number | null;
    };
  };
};

export type MarketplaceRouteParams = {
  query: string;
  model_id?: string;
};

export type MarketplaceRouteData = {
  app_id: string;
  confidence: number;
  reason: string;
  params: Record<string, unknown>;
  suggestions: string[];
};

export type NutritionFollowupParams = {
  analysis: NutritionAnalysisData;
  question: string;
  model_id?: string;
};

export type NutritionFollowupData = {
  answer: string;
};

export type ConsumptionAnalysisParams = {
  file_id: string;
  serving_grams: number;
  model_id?: string;
};

export type ConsumptionManualParams = {
  total_grams: number;
  serving_grams: number;
};

export type ConsumptionCorrectionParams = {
  text: string;
  serving_grams: number;
};

export type ConsumptionAnalysisData = {
  recognized: boolean;
  ocr_text?: string | null;
  confidence: number;
  total_grams?: number | null;
  serving_grams?: number | null;
  servings?: number | null;
  full_servings?: number | null;
  message: string;
};

export type TranslationParams = {
  text?: string;
  file_id?: string;
  target_language: string;
  style: "plain" | "formal" | "casual" | "technical";
  model_id?: string;
};

export type TranslationData = {
  detected_language: string;
  target_language: string;
  translated_text: string;
  notes: string[];
};

export type DocumentConvertParams = {
  file_id: string;
  target_format: "pdf" | "docx" | "md" | "txt";
};

export type DocumentConvertData = {
  result_file_id: string;
  result_filename: string;
  source_format: string;
  target_format: string;
  download_ready: boolean;
};

export type WatermarkAddParams = {
  file_id: string;
  watermark_type?: "text" | "image";
  text?: string;
  watermark_file_id?: string;
  opacity?: number;
  rotation?: number;
  tile?: boolean;
};

export type WatermarkRemoveParams = {
  file_id: string;
  watermark_text?: string;
  mode?: "auto" | "text" | "images";
  model_id?: string;
};

export type WatermarkResultData = {
  result_file_id: string;
  result_filename: string;
  download_ready: boolean;
  method?: string;
};

// ==================== 文件模块类型 ====================

/**
 * 文件信息
 */
export type FileInfo = {
  id: string;
  filename: string;
  filepath: string;
  key: string;
  extension: string;
  content_type: string;
  size: number;
  [key: string]: unknown;
};

/**
 * 文件上传请求参数
 */
export type FileUploadParams = {
  file: File;
  session_id?: string;
};

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
  knowledge_base_id?: string;
  mode?: SessionMode;
  operator_scope?: "owned" | "third_party_saas";
  operator_domains?: string[];
  gate_profile?: "loose" | "standard" | "strict";
  [key: string]: unknown;
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
  [key: string]: unknown;
};

export type ClarifyAnswer = {
  question_id: string;
  prompt?: string;
  option_ids: string[];
  option_labels: string[];
  custom_text?: string;
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

export type ClarifyOption = {
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
export type EventChannel = "ui" | "debug" | "runtime";

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
  | { type: "assistant_notice"; data: { message: string } & EventMeta }
  | { type: "session_status"; data: { status: SessionStatus } & EventMeta }
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

export type CheckpointAnchorType = "user_message" | "step";

export type SessionCheckpoint = {
  id: string;
  session_id: string;
  anchor_type: CheckpointAnchorType;
  anchor_event_id: string;
  label: string;
  created_at: string;
};

// ==================== 交付物 ====================

export type DeliveryArtifactKind = "doc" | "web";
export type DeliveryArtifactStatus = "draft" | "updated" | "final";

export type DeliveryArtifact = {
  id: string;
  session_id: string;
  kind: DeliveryArtifactKind;
  title: string;
  storage_ref: string;
  version_refs: string[];
  status: DeliveryArtifactStatus;
  created_at: string;
  updated_at: string;
};

export type DeliveryArtifactsData = {
  artifacts: DeliveryArtifact[];
};

export type DeliveryArtifactContent = {
  content: string;
  content_type: string;
  incomplete?: boolean;
};

export type DeliveryArtifactShare = {
  share_token: string;
  share_url: string;
};

export type ArtifactEventSummary = {
  artifact_id: string;
  kind: DeliveryArtifactKind;
  title: string;
  status: DeliveryArtifactStatus;
  storage_ref: string;
  version: number;
};

// ==================== 通知 ====================

export type Notification = {
  id: string;
  user_id: string;
  type: string;
  session_id?: string | null;
  artifact_id?: string | null;
  job_id?: string | null;
  message: string;
  read: boolean;
  created_at: string;
};

export type NotificationsData = {
  notifications: Notification[];
  unread_count: number;
};

export type PendingPlanUpdate = {
  plan: PlanEvent;
};

// ==================== 自动化任务 ====================

export type ScheduledJobTriggerType = "cron" | "interval" | "webhook";

export type NotifyChannel = {
  type: string;
  server_name: string;
  channel_arg: string;
};

export type ScheduledJob = {
  id: string;
  name: string;
  owner_user_id: string;
  trigger_type: ScheduledJobTriggerType | string;
  trigger_spec: string;
  prompt_template: string;
  skill_id?: string | null;
  model_id?: string | null;
  codebase_id?: string | null;
  knowledge_base_id?: string | null;
  operator_scope?: "owned" | "third_party_saas" | null;
  operator_domains?: string[];
  gate_profile?: "loose" | "standard" | "strict" | null;
  notify_channels: NotifyChannel[];
  enabled: boolean;
  next_run_at?: string | null;
  last_run_at?: string | null;
  last_run_status?: string | null;
  last_run_session_id?: string | null;
  last_run_error?: string | null;
  webhook_token?: string | null;
};

export type ScheduledJobsData = {
  jobs: ScheduledJob[];
};

export type CreateScheduledJobParams = {
  name: string;
  trigger_type?: ScheduledJobTriggerType;
  trigger_spec?: string;
  prompt_template: string;
  skill_id?: string | null;
  model_id?: string | null;
  codebase_id?: string | null;
  knowledge_base_id?: string | null;
  notify_channels?: NotifyChannel[];
  operator_scope?: "owned" | "third_party_saas" | null;
  operator_domains?: string[];
  gate_profile?: "loose" | "standard" | "strict" | null;
  enabled?: boolean;
};

export type UpdateScheduledJobParams = Partial<CreateScheduledJobParams>;

export type CreateScheduledJobResult = {
  job: ScheduledJob;
  webhook_secret?: string | null;
};

export type ApprovalEventData = Extract<SSEEventData, { type: "approval" }>["data"];

export type SessionCheckpointsData = {
  checkpoints: SessionCheckpoint[];
};

/**
 * 会话文件信息
 */
export type SessionFile = {
  id: string;
  filename: string;
  filepath: string;
  key: string;
  extension: string;
  content_type: string;
  size: number;
  [key: string]: unknown;
};

/**
 * 查看文件内容请求参数
 */
export type ViewFileParams = {
  filepath: string;
  [key: string]: unknown;
};

/**
 * 查看 Shell 输出请求参数
 */
export type ViewShellParams = {
  shell_session_id: string;
  [key: string]: unknown;
};

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

export type ArtifactKind =
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

// ==================== 文档知识库 ====================

export type KnowledgeBaseStatus =
  | "pending"
  | "parsing"
  | "chunking"
  | "indexing"
  | "graph_building"
  | "ready"
  | "failed";

export type KnowledgeDocumentStatus = "pending" | "parsing" | "ready" | "failed";
export type KnowledgeSourceType = "upload" | "zip" | "web" | "confluence" | "feishu";

export type KnowledgeBase = {
  id: string;
  name: string;
  status: KnowledgeBaseStatus;
  doc_count: number;
  chunk_count: number;
  ingest_task_id?: string | null;
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
};

export type KnowledgeSessionData = {
  session_id: string;
  knowledge_base_id: string;
  mode: SessionMode;
};

export type ReadKnowledgeDocumentData = {
  document: KnowledgeDocument;
  content: string;
};
