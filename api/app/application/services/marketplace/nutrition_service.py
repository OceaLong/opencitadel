#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Any, Dict, List, Optional

from app.application.services.marketplace.utils import (
    analyze_image_with_llm,
    load_nutrition_foods,
    match_food_entry,
    validate_image,
)
from app.domain.external.llm import LLM

logger = logging.getLogger(__name__)

NUTRITION_PROMPT = """你是营养分析助手。请分析图片中的餐食，仅返回 JSON，不要其他文字：
{
  "items": [{"name": "菜品名", "estimated_grams": 150, "confidence": 0.8}],
  "meal_summary": "简短描述"
}
要求：识别主要菜品，estimated_grams 为估计食用克数。"""


class NutritionService:
    """AI 视觉营养分析。"""

    def __init__(self) -> None:
        self._foods = load_nutrition_foods()

    async def analyze(
            self,
            llm: LLM,
            image_bytes: bytes,
            mime_type: str,
            *,
            weight_kg: Optional[float] = None,
            goal: Optional[str] = None,
    ) -> Dict[str, Any]:
        validate_image(mime_type, len(image_bytes))
        vision_data = await analyze_image_with_llm(llm, image_bytes, mime_type, NUTRITION_PROMPT)
        items = vision_data.get("items") or []
        if not items and vision_data.get("name"):
            items = [vision_data]

        nutrition_items: List[Dict[str, Any]] = []
        totals = {"calories": 0.0, "protein": 0.0, "fat": 0.0, "carbs": 0.0}

        for item in items:
            name = str(item.get("name") or "未知菜品")
            grams = float(item.get("estimated_grams") or 150)
            confidence = float(item.get("confidence") or 0.6)
            food = match_food_entry(name, self._foods)
            if food:
                per100 = food["per_100g"]
                ratio = grams / 100.0
                entry = {
                    "name": food["name"],
                    "grams": round(grams, 1),
                    "confidence": confidence,
                    "calories": round(per100["calories"] * ratio, 1),
                    "protein": round(per100["protein"] * ratio, 1),
                    "fat": round(per100["fat"] * ratio, 1),
                    "carbs": round(per100["carbs"] * ratio, 1),
                }
            else:
                entry = {
                    "name": name,
                    "grams": round(grams, 1),
                    "confidence": confidence,
                    "calories": round(grams * 1.5, 1),
                    "protein": round(grams * 0.08, 1),
                    "fat": round(grams * 0.05, 1),
                    "carbs": round(grams * 0.15, 1),
                }
            nutrition_items.append(entry)
            for key in totals:
                totals[key] += entry[key]

        for key in totals:
            totals[key] = round(totals[key], 1)

        assessment = self._assess(totals, weight_kg=weight_kg, goal=goal)

        return {
            "meal_summary": vision_data.get("meal_summary") or "已识别餐食营养信息",
            "items": nutrition_items,
            "totals": totals,
            "assessment": assessment,
        }

    def _assess(
            self,
            totals: Dict[str, float],
            *,
            weight_kg: Optional[float],
            goal: Optional[str],
    ) -> Dict[str, Any]:
        calories = totals["calories"]
        protein = totals["protein"]
        tips: List[str] = []
        lights: Dict[str, str] = {}

        if calories > 700:
            lights["calories"] = "red"
            tips.append("热量较高，建议分餐或减少高油主食")
        elif calories > 600:
            lights["calories"] = "yellow"
            tips.append("热量偏高，减脂期建议控制份量")
        else:
            lights["calories"] = "green"

        if protein < 30:
            lights["protein"] = "red"
            tips.append("蛋白质摄入不足，增肌期建议补充优质蛋白")
        elif protein < 20:
            lights["protein"] = "yellow"
            tips.append("蛋白质略低，可搭配鸡蛋/鸡胸/豆制品")
        else:
            lights["protein"] = "green"

        overall = "green"
        if "red" in lights.values():
            overall = "red"
        elif "yellow" in lights.values():
            overall = "yellow"

        ratios: Dict[str, Optional[float]] = {
            "calories_per_kg": None,
            "protein_per_kg": None,
        }
        if weight_kg and weight_kg > 0:
            ratios["calories_per_kg"] = round(calories / weight_kg, 2)
            ratios["protein_per_kg"] = round(protein / weight_kg, 2)
            if goal == "cut" and ratios["calories_per_kg"] and ratios["calories_per_kg"] > 8:
                tips.append(f"按体重计算单餐热量占比偏高（{ratios['calories_per_kg']} kcal/kg）")
            if goal == "bulk" and ratios["protein_per_kg"] and ratios["protein_per_kg"] < 0.35:
                tips.append(f"按体重计算蛋白质占比偏低（{ratios['protein_per_kg']} g/kg）")

        return {
            "overall": overall,
            "lights": lights,
            "tips": tips,
            "goal": goal,
            "ratios": ratios,
        }
