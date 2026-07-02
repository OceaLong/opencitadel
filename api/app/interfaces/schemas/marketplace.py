#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class MarketplaceAppResponse(BaseModel):
    id: str
    name: str
    description: str
    icon: str
    category: str
    tags: List[str] = Field(default_factory=list)
    featured: bool = False
    accent: str = "blue"
    needs_vision: bool = False
    model_dependency: Literal["none", "optional", "required"] = "optional"
    examples: List[str] = Field(default_factory=list)


class MarketplaceAppsResponse(BaseModel):
    apps: List[MarketplaceAppResponse]


class VideoSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=200)


class VideoSearchResultItem(BaseModel):
    title: str
    platform: str
    icon: str
    url: str
    quality: str
    condition: str
    trust_score: float
    source_type: Optional[str] = None
    recommendation_reason: Optional[str] = None


class VideoSearchStats(BaseModel):
    crawled_candidates: int
    filtered_risk_sources: int
    legal_results: int


class VideoSearchResponse(BaseModel):
    query: str
    copyright_notice: str
    results: List[VideoSearchResultItem]
    stats: VideoSearchStats


class MarketplaceRouteRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    model_id: Optional[str] = None


class MarketplaceRouteResponse(BaseModel):
    app_id: str
    confidence: float = Field(default=0.0, ge=0, le=1)
    reason: str
    params: Dict[str, Any] = Field(default_factory=dict)
    suggestions: List[str] = Field(default_factory=list)


class NutritionAnalysisRequest(BaseModel):
    file_id: str
    model_id: Optional[str] = None
    weight_kg: Optional[float] = Field(default=None, gt=0, le=300)
    goal: Optional[Literal["cut", "bulk", "maintain"]] = None


class NutritionItem(BaseModel):
    name: str
    grams: float
    confidence: float
    calories: float
    protein: float
    fat: float
    carbs: float


class NutritionTotals(BaseModel):
    calories: float
    protein: float
    fat: float
    carbs: float


class NutritionAssessment(BaseModel):
    overall: Literal["green", "yellow", "red"]
    lights: dict
    tips: List[str]
    goal: Optional[str] = None
    ratios: dict


class NutritionAnalysisResponse(BaseModel):
    meal_summary: str
    items: List[NutritionItem]
    totals: NutritionTotals
    assessment: NutritionAssessment


class NutritionFollowupRequest(BaseModel):
    analysis: NutritionAnalysisResponse
    question: str = Field(..., min_length=1, max_length=500)
    model_id: Optional[str] = None


class NutritionFollowupResponse(BaseModel):
    answer: str


class ConsumptionAnalysisRequest(BaseModel):
    file_id: str
    serving_grams: float = Field(..., gt=0, le=10000)
    model_id: Optional[str] = None


class ConsumptionManualRequest(BaseModel):
    total_grams: float = Field(..., gt=0, le=1000000)
    serving_grams: float = Field(..., gt=0, le=10000)


class ConsumptionAnalysisResponse(BaseModel):
    recognized: bool
    ocr_text: Optional[str] = None
    confidence: float
    total_grams: Optional[float] = None
    serving_grams: Optional[float] = None
    servings: Optional[float] = None
    full_servings: Optional[int] = None
    message: str


class ConsumptionCorrectionRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=300)
    serving_grams: float = Field(..., gt=0, le=10000)


class DocumentQaRequest(BaseModel):
    file_id: str
    question: str = Field(..., min_length=1, max_length=1000)
    model_id: Optional[str] = None


class DocumentQaResponse(BaseModel):
    answer: str
    source_summary: str


class TranslationRequest(BaseModel):
    text: Optional[str] = Field(default=None, max_length=10000)
    file_id: Optional[str] = None
    target_language: str = Field(default="中文", max_length=50)
    style: Literal["plain", "formal", "casual", "technical"] = "plain"
    model_id: Optional[str] = None


class TranslationResponse(BaseModel):
    detected_language: str
    target_language: str
    translated_text: str
    notes: List[str] = Field(default_factory=list)


class DocumentConvertRequest(BaseModel):
    file_id: str
    target_format: Literal["pdf", "docx", "md", "txt"]


class DocumentConvertResponse(BaseModel):
    result_file_id: str
    result_filename: str
    source_format: str
    target_format: str
    download_ready: bool = True


class WatermarkAddRequest(BaseModel):
    file_id: str
    watermark_type: Literal["text", "image"] = "text"
    text: Optional[str] = Field(default=None, max_length=200)
    watermark_file_id: Optional[str] = None
    opacity: float = Field(default=0.3, ge=0.05, le=1.0)
    rotation: float = Field(default=45.0, ge=-180, le=180)
    tile: bool = True


class WatermarkRemoveRequest(BaseModel):
    file_id: str
    watermark_text: Optional[str] = Field(default=None, max_length=200)
    mode: Literal["auto", "text", "images"] = "auto"
    model_id: Optional[str] = None


class WatermarkResultResponse(BaseModel):
    result_file_id: str
    result_filename: str
    download_ready: bool = True
    method: Optional[str] = None


class FortuneInputProfile(BaseModel):
    nickname: Optional[str] = Field(default=None, max_length=50)
    birth_date: Optional[str] = Field(default=None, max_length=20)
    birth_time: Optional[str] = Field(default=None, max_length=20)
    birth_place: Optional[str] = Field(default=None, max_length=100)


class FortunePredictionRequest(BaseModel):
    mode: Literal["fortune", "lottery", "divination", "astrology"] = "fortune"
    question: str = Field(..., min_length=1, max_length=500)
    input_profile: FortuneInputProfile = Field(default_factory=FortuneInputProfile)
    model_id: Optional[str] = None


class FortuneSection(BaseModel):
    heading: str
    content: str


class FortuneLuckyItems(BaseModel):
    color: str = ""
    number: str = ""
    keyword: str = ""
    element: str = ""


class FortunePredictionResult(BaseModel):
    mode: str
    title: str
    summary: str
    sections: List[FortuneSection] = Field(default_factory=list)
    lucky_items: FortuneLuckyItems = Field(default_factory=FortuneLuckyItems)
    disclaimer: str = "本结果仅供娱乐参考，请理性看待。"


class FortunePredictionResponse(BaseModel):
    share_id: str
    mode: str
    question: str
    input_profile: Dict[str, Any] = Field(default_factory=dict)
    result: FortunePredictionResult
    created_at: str
