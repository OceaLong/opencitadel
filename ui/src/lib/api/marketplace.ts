import { get, post } from "./fetch";
import { authenticatedFetch } from "./fetch";
import type {
  ConsumptionAnalysisData,
  ConsumptionAnalysisParams,
  ConsumptionCorrectionParams,
  ConsumptionManualParams,
  DocumentConvertData,
  DocumentConvertParams,
  DocumentQaData,
  DocumentQaParams,
  FortunePredictionData,
  FortunePredictionParams,
  MarketplaceAppsData,
  MarketplaceRouteData,
  MarketplaceRouteParams,
  NutritionAnalysisData,
  NutritionAnalysisParams,
  NutritionFollowupData,
  NutritionFollowupParams,
  TranslationData,
  TranslationParams,
  VideoSearchData,
  VideoSearchParams,
  WatermarkAddParams,
  WatermarkRemoveParams,
  WatermarkResultData,
} from "./types";

export const marketplaceApi = {
  listApps: (): Promise<MarketplaceAppsData> => get<MarketplaceAppsData>("/marketplace/apps"),

  searchVideos: (params: VideoSearchParams): Promise<VideoSearchData> =>
    post<VideoSearchData>("/marketplace/video/search", params, { timeout: 90_000 }),

  routeRequest: (params: MarketplaceRouteParams): Promise<MarketplaceRouteData> =>
    post<MarketplaceRouteData>("/marketplace/assistant/route", params),

  analyzeNutrition: (params: NutritionAnalysisParams): Promise<NutritionAnalysisData> =>
    post<NutritionAnalysisData>("/marketplace/nutrition/analyze", params),

  answerNutritionFollowup: (params: NutritionFollowupParams): Promise<NutritionFollowupData> =>
    post<NutritionFollowupData>("/marketplace/nutrition/followup", params),

  analyzeConsumption: (params: ConsumptionAnalysisParams): Promise<ConsumptionAnalysisData> =>
    post<ConsumptionAnalysisData>("/marketplace/consumption/analyze", params),

  calculateConsumption: (params: ConsumptionManualParams): Promise<ConsumptionAnalysisData> =>
    post<ConsumptionAnalysisData>("/marketplace/consumption/calculate", params),

  correctConsumption: (params: ConsumptionCorrectionParams): Promise<ConsumptionAnalysisData> =>
    post<ConsumptionAnalysisData>("/marketplace/consumption/correct", params),

  askDocumentQuestion: (params: DocumentQaParams): Promise<DocumentQaData> =>
    post<DocumentQaData>("/marketplace/document-qa/ask", params),

  translate: (params: TranslationParams): Promise<TranslationData> =>
    post<TranslationData>("/marketplace/translation/translate", params),

  convertDocument: (params: DocumentConvertParams): Promise<DocumentConvertData> =>
    post<DocumentConvertData>("/marketplace/convert", params),

  addWatermark: (params: WatermarkAddParams): Promise<WatermarkResultData> =>
    post<WatermarkResultData>("/marketplace/watermark/add", params),

  removeWatermark: (params: WatermarkRemoveParams): Promise<WatermarkResultData> =>
    post<WatermarkResultData>("/marketplace/watermark/remove", params),

  predictFortune: (params: FortunePredictionParams): Promise<FortunePredictionData> =>
    post<FortunePredictionData>("/marketplace/fortune/predict", params),

  predictFortuneStream: async (
    params: FortunePredictionParams,
    handlers: {
      onDelta: (text: string) => void;
      onDone: (data: FortunePredictionData) => void;
      onError: (message: string) => void;
    },
  ): Promise<void> => {
    const response = await authenticatedFetch("/marketplace/fortune/predict/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(params),
    });

    if (!response.ok || !response.body) {
      handlers.onError("流式预测失败");
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";

      for (const part of parts) {
        const lines = part.split("\n");
        let event = "message";
        let data = "";
        for (const line of lines) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (!data) continue;
        try {
          const parsed = JSON.parse(data) as Record<string, unknown>;
          if (event === "delta" && typeof parsed.text === "string") {
            handlers.onDelta(parsed.text);
          } else if (event === "done") {
            handlers.onDone(parsed as unknown as FortunePredictionData);
          } else if (event === "error") {
            handlers.onError(String(parsed.message ?? "预测失败"));
          }
        } catch {
          handlers.onError("结果解析失败");
        }
      }
    }
  },

  getFortuneShare: (shareId: string): Promise<FortunePredictionData> =>
    get<FortunePredictionData>(`/marketplace/fortune/share/${shareId}`),
};
