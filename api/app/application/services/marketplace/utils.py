#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.domain.external.llm import LLM
from app.domain.services import vision_service
from app.domain.utils.vision import build_image_content_part, is_image_mime
from app.infrastructure.external.json_parser.repair_json_parser import RepairJSONParser

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_MIMES = {"image/jpeg", "image/jpg", "image/png"}

NET_CONTENT_PATTERN = re.compile(
    r"(?:净含量|净重|Net\s*Wt\.?|Net\s*Weight|含量)[：:\s]*"
    r"(\d+(?:\.\d+)?)\s*(g|kg|ml|mL|l|L|克|千克|毫升|升)",
    re.IGNORECASE,
)

UNIT_TO_GRAMS = {
    "g": 1.0, "克": 1.0,
    "kg": 1000.0, "千克": 1000.0,
    "ml": 1.0, "mL": 1.0, "毫升": 1.0,
    "l": 1000.0, "L": 1000.0, "升": 1000.0,
}


def validate_image(mime_type: str, size: int) -> None:
    if mime_type.lower() not in ALLOWED_IMAGE_MIMES and not is_image_mime(mime_type):
        raise ValueError("仅支持 JPG/PNG 图片")
    if size > MAX_IMAGE_BYTES:
        raise ValueError("图片大小不能超过 5MB")


def parse_net_content(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    match = NET_CONTENT_PATTERN.search(text)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2)
    factor = UNIT_TO_GRAMS.get(unit, UNIT_TO_GRAMS.get(unit.lower(), 1.0))
    grams = value * factor
    return {
        "raw_text": match.group(0),
        "value": value,
        "unit": unit,
        "grams": grams,
        "confidence": 0.85,
    }


def calculate_servings(total_grams: float, serving_grams: float) -> Dict[str, Any]:
    if serving_grams <= 0:
        raise ValueError("单次食用量必须大于 0")
    count = total_grams / serving_grams
    return {
        "total_grams": round(total_grams, 1),
        "serving_grams": round(serving_grams, 1),
        "servings": round(count, 1),
        "full_servings": int(count),
    }


def load_nutrition_foods() -> List[Dict[str, Any]]:
    data_path = Path(__file__).resolve().parents[2] / "data" / "nutrition_foods.json"
    with data_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    return payload.get("foods", [])


def match_food_entry(name: str, foods: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    normalized = (name or "").strip().lower()
    if not normalized:
        return None
    for food in foods:
        candidates = [food["name"].lower(), *[a.lower() for a in food.get("aliases", [])]]
        if any(c in normalized or normalized in c for c in candidates):
            return food
    return None


async def analyze_image_with_llm(
        llm: LLM,
        image_bytes: bytes,
        mime_type: str,
        prompt: str,
        *,
        schema: Optional[Dict[str, Any]] = None,
        max_retries: int = 2,
) -> Dict[str, Any]:
    if not vision_service.vision_enabled(llm):
        raise ValueError("当前默认模型未开启多模态能力，请在设置中选择支持视觉的模型")

    capabilities = vision_service.resolve_capabilities(llm)
    if len(image_bytes) > capabilities.max_image_bytes:
        image_bytes = vision_service._compress_image_bytes(
            image_bytes, mime_type, capabilities.max_image_bytes,
        )

    parser = RepairJSONParser()
    last_error: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        retry_hint = ""
        if attempt > 0:
            retry_hint = "\n\n请严格输出合法 JSON，不要包含 markdown 代码块。"
        image_part = build_image_content_part(image_bytes, mime_type)
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt + retry_hint},
                image_part,
            ],
        }]
        try:
            response = await llm.invoke(messages)
            content = response.get("content") or response.get("reasoning_content") or ""
            if not content:
                raise ValueError("模型未返回有效分析结果")
            parsed = await parser.invoke(content, default_value={})
            if schema:
                validated = validate_json_schema(parsed, schema)
                if validated is not None:
                    return validated
                raise ValueError("JSON 结构校验失败")
            return parsed
        except Exception as exc:
            last_error = exc
            logger.warning("vision LLM 调用失败 attempt=%s: %s", attempt, exc)
    raise ValueError(str(last_error) if last_error else "模型未返回有效分析结果")


def validate_json_schema(data: Dict[str, Any], schema: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """轻量 JSON schema 校验（required 字段 + 类型）。"""
    if not isinstance(data, dict):
        return None
    required = schema.get("required") or []
    properties = schema.get("properties") or {}
    for field in required:
        if field not in data:
            return None
    for field, spec in properties.items():
        if field not in data:
            continue
        expected = spec.get("type")
        value = data[field]
        if expected == "array" and not isinstance(value, list):
            return None
        if expected == "string" and not isinstance(value, str):
            return None
        if expected == "number" and not isinstance(value, (int, float)):
            return None
    return data


async def analyze_images_with_llm(
        llm: LLM,
        images: List[tuple[bytes, str]],
        prompt: str,
        *,
        schema: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """多图 vision 分析。"""
    if not images:
        raise ValueError("至少需要一张图片")
    capabilities = vision_service.resolve_capabilities(llm)
    parts: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image_bytes, mime_type in images[: capabilities.max_images_per_request]:
        if len(image_bytes) > capabilities.max_image_bytes:
            image_bytes = vision_service._compress_image_bytes(
                image_bytes, mime_type, capabilities.max_image_bytes,
            )
        parts.append(build_image_content_part(image_bytes, mime_type))
    response = await llm.invoke([{"role": "user", "content": parts}])
    content = response.get("content") or response.get("reasoning_content") or ""
    parser = RepairJSONParser()
    parsed = await parser.invoke(content, default_value={})
    if schema:
        validated = validate_json_schema(parsed, schema)
        if validated is None:
            raise ValueError("JSON 结构校验失败")
        return validated
    return parsed
