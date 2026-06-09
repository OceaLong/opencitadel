import { get, post } from "./fetch";
import type {
  ConsumptionAnalysisData,
  ConsumptionAnalysisParams,
  ConsumptionCorrectionParams,
  ConsumptionManualParams,
  DocumentQaData,
  DocumentQaParams,
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
} from "./types";

export const marketplaceApi = {
  listApps: (): Promise<MarketplaceAppsData> => get<MarketplaceAppsData>("/marketplace/apps"),

  searchVideos: (params: VideoSearchParams): Promise<VideoSearchData> =>
    post<VideoSearchData>("/marketplace/video/search", params),

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
};
