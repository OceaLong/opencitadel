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

export type NutritionAnalysisParams = {
  file_id: string;
  model_id?: string;
  weight_kg?: number;
  goal?: "cut" | "bulk" | "maintain";
};

type NutritionItem = {
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
