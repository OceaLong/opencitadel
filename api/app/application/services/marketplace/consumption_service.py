#!/usr/bin/env python
# -*- coding: utf-8 -*-
import logging
from typing import Any, Dict, Optional

from app.application.services.marketplace.utils import (
    analyze_image_with_llm,
    calculate_servings,
    parse_net_content,
    validate_image,
)
from app.domain.external.llm import LLM

logger = logging.getLogger(__name__)

OCR_PROMPT = """你是包装标签 OCR 助手。请从图片中提取净含量信息，仅返回 JSON：
{
  "net_content_text": "识别到的原文，如 净含量：1000g",
  "value": 1000,
  "unit": "g",
  "confidence": 0.85
}
若无法识别，value 设为 null，confidence 设为 0。"""


class ConsumptionService:
    """实物消耗计算器。"""

    async def analyze_from_image(
            self,
            llm: LLM,
            image_bytes: bytes,
            mime_type: str,
            serving_grams: float,
    ) -> Dict[str, Any]:
        validate_image(mime_type, len(image_bytes))
        if serving_grams <= 0:
            raise ValueError("单次食用量必须大于 0")

        vision_data = await analyze_image_with_llm(llm, image_bytes, mime_type, OCR_PROMPT)
        parsed = self._parse_vision_result(vision_data)

        if not parsed:
            return {
                "recognized": False,
                "ocr_text": vision_data.get("net_content_text"),
                "confidence": float(vision_data.get("confidence") or 0),
                "message": "未能自动识别净含量，请手动输入总量后计算",
                "serving_grams": serving_grams,
            }

        result = calculate_servings(parsed["grams"], serving_grams)
        return {
            "recognized": True,
            "ocr_text": parsed.get("raw_text") or vision_data.get("net_content_text"),
            "confidence": parsed.get("confidence", 0.8),
            "total_grams": result["total_grams"],
            "serving_grams": result["serving_grams"],
            "servings": result["servings"],
            "full_servings": result["full_servings"],
            "message": (
                f"这包食品约可吃 {result['full_servings']} 次"
                f"（按每次 {result['serving_grams']}g 计算，共约 {result['servings']} 次）"
            ),
        }

    def calculate_manual(self, total_grams: float, serving_grams: float) -> Dict[str, Any]:
        if total_grams <= 0:
            raise ValueError("总量必须大于 0")
        result = calculate_servings(total_grams, serving_grams)
        return {
            "recognized": True,
            "ocr_text": None,
            "confidence": 1.0,
            "total_grams": result["total_grams"],
            "serving_grams": result["serving_grams"],
            "servings": result["servings"],
            "full_servings": result["full_servings"],
            "message": (
                f"这包食品约可吃 {result['full_servings']} 次"
                f"（按每次 {result['serving_grams']}g 计算，共约 {result['servings']} 次）"
            ),
        }

    def _parse_vision_result(self, vision_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        value = vision_data.get("value")
        unit = vision_data.get("unit")
        if value is not None and unit:
            from app.application.services.marketplace.utils import UNIT_TO_GRAMS
            factor = UNIT_TO_GRAMS.get(str(unit), UNIT_TO_GRAMS.get(str(unit).lower(), 1.0))
            grams = float(value) * factor
            return {
                "raw_text": vision_data.get("net_content_text"),
                "grams": grams,
                "confidence": float(vision_data.get("confidence") or 0.7),
            }

        text = vision_data.get("net_content_text") or ""
        parsed = parse_net_content(text)
        if parsed:
            return parsed
        return None
