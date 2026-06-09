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
export type SessionStatus = "pending" | "running" | "waiting" | "completed" | "cancelled";

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
  created_at?: string;
  updated_at?: string;
};

export type LLMModelsData = {
  models: LLMModel[];
};

export type CreateLLMModelParams = {
  display_name: string;
  provider: LLMProvider;
  base_url: string;
  api_key?: string;
  model_name: string;
  temperature?: number;
  max_tokens?: number;
  extra_params?: Record<string, unknown>;
  capabilities?: ModelCapabilities;
  supports_multimodal?: boolean;
  is_default?: boolean;
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
  recommended_model_id?: string | null;
  agent_params: SkillAgentParams;
  examples: string[];
  is_builtin: boolean;
  enabled: boolean;
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
 * LLM 配置
 */
export type LLMConfig = {
  base_url?: string;
  api_key?: string;
  model_name?: string;
  temperature?: number;
  max_tokens?: number;
  [key: string]: unknown;
};

/**
 * Agent 通用配置
 */
export type AgentConfig = {
  max_iterations?: number;
  max_retries?: number;
  max_search_results?: number;
  [key: string]: unknown;
};

/**
 * MCP 服务器列表项（GET 响应）
 */
export type ListMCPServerItem = {
  server_name: string;
  enabled: boolean;
  transport: MCPTransport;
  tools: string[];
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
  examples: string[];
};

export type MarketplaceAppsData = {
  apps: MarketplaceApp[];
};

export type VideoSearchParams = {
  query: string;
};

export type VideoSearchResult = {
  title: string;
  platform: string;
  icon: string;
  url: string;
  quality: string;
  condition: string;
  trust_score: number;
  source_type?: string;
  recommendation_reason?: string | null;
};

export type VideoSearchData = {
  query: string;
  copyright_notice: string;
  results: VideoSearchResult[];
  stats: {
    crawled_candidates: number;
    filtered_risk_sources: number;
    legal_results: number;
  };
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

export type DocumentQaParams = {
  file_id: string;
  question: string;
  model_id?: string;
};

export type DocumentQaData = {
  answer: string;
  source_summary: string;
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
export type CreateSessionParams = {
  title?: string;
  model_id?: string;
  skill_id?: string;
  thinking_enabled?: boolean;
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

/**
 * 聊天请求参数
 * message 为空时用于流式拉取未完成任务的事件列表
 */
export type ChatParams = {
  message?: string;
  attachments?: string[];
  event_id?: string;
  model_id?: string;
  skill_id?: string;
  thinking_enabled?: boolean;
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

export type SessionDetail = Session & {
  events?: SSEEventData[];
  events_next_cursor?: number | null;
  model_id?: string | null;
  skill_id?: string | null;
  thinking_enabled?: boolean;
  model?: LLMModel | null;
  skill?: SkillSummary | null;
  token_usage?: TokenUsageSummary | null;
};

export type UpdateSessionConfigParams = {
  model_id?: string;
  skill_id?: string;
  thinking_enabled?: boolean;
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
  | "error";

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
  | { type: "error"; data: { error: string } & EventMeta };

/**
 * SSE 事件处理器
 */
export type SSEEventHandler = (event: SSEEventData) => void;

export type SessionEventsPage = {
  events: SSEEventData[];
  next_cursor?: number | null;
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
