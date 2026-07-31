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
  team_id?: string | null;
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
  team_id?: string | null;
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
