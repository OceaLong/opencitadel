#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class MarketplaceAppResponse(BaseModel):
    id: str
    name: str
    description: str
    icon: str
    category: str


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


class VideoSearchStats(BaseModel):
    crawled_candidates: int
    filtered_risk_sources: int
    legal_results: int


class VideoSearchResponse(BaseModel):
    query: str
    copyright_notice: str
    results: List[VideoSearchResultItem]
    stats: VideoSearchStats


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
