#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

from app.domain.external.llm import LLM
from app.domain.services import vision_service
from app.domain.utils.vision import build_image_content_part, is_image_mime
from app.infrastructure.external.json_parser.repair_json_parser import RepairJSONParser

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_MIMES = {"image/jpeg", "image/jpg", "image/png"}

LEGAL_VIDEO_DOMAINS = {
    "youtube.com": {"name": "YouTube", "icon": "▶️", "condition": "官方/免费公开"},
    "www.youtube.com": {"name": "YouTube", "icon": "▶️", "condition": "官方/免费公开"},
    "youtu.be": {"name": "YouTube", "icon": "▶️", "condition": "官方/免费公开"},
    "bilibili.com": {"name": "哔哩哔哩", "icon": "📺", "condition": "正版/限免"},
    "www.bilibili.com": {"name": "哔哩哔哩", "icon": "📺", "condition": "正版/限免"},
    "v.qq.com": {"name": "腾讯视频", "icon": "🎬", "condition": "正版/限免"},
    "youku.com": {"name": "优酷", "icon": "🎬", "condition": "正版/限免"},
    "www.youku.com": {"name": "优酷", "icon": "🎬", "condition": "正版/限免"},
    "iqiyi.com": {"name": "爱奇艺", "icon": "🎬", "condition": "正版/限免"},
    "www.iqiyi.com": {"name": "爱奇艺", "icon": "🎬", "condition": "正版/限免"},
    "mgtv.com": {"name": "芒果TV", "icon": "🎬", "condition": "正版/限免"},
    "www.mgtv.com": {"name": "芒果TV", "icon": "🎬", "condition": "正版/限免"},
    "tv.cctv.com": {"name": "央视网", "icon": "📡", "condition": "官方免费"},
    "archive.org": {"name": "Internet Archive", "icon": "📚", "condition": "公共领域"},
}

BLOCKED_VIDEO_KEYWORDS = (
    "网盘", "磁力", "种子", "盗版", "资源站", "免费看全集",
    "pan.baidu", "aliyundrive", "115.com", "magnet:", "torrent",
)

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


def extract_domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        return (parsed.netloc or "").lower()
    except Exception:
        return ""


def is_legal_video_url(url: str) -> bool:
    domain = extract_domain(url)
    if not domain:
        return False
    if any(keyword in url.lower() for keyword in BLOCKED_VIDEO_KEYWORDS):
        return False
    for allowed in LEGAL_VIDEO_DOMAINS:
        if domain == allowed or domain.endswith(f".{allowed}"):
            return True
    return False


def get_provider_meta(url: str) -> Dict[str, str]:
    domain = extract_domain(url)
    for key, meta in LEGAL_VIDEO_DOMAINS.items():
        if domain == key or domain.endswith(f".{key}"):
            return meta
    return {"name": domain, "icon": "🔗", "condition": "正版来源"}


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
) -> Dict[str, Any]:
    if not vision_service.vision_enabled(llm):
        raise ValueError("当前默认模型未开启多模态能力，请在设置中选择支持视觉的模型")

    capabilities = vision_service.resolve_capabilities(llm)
    if len(image_bytes) > capabilities.max_image_bytes:
        image_bytes = vision_service._compress_image_bytes(
            image_bytes, mime_type, capabilities.max_image_bytes,
        )

    image_part = build_image_content_part(image_bytes, mime_type)
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            image_part,
        ],
    }]
    response = await llm.invoke(messages)
    content = response.get("content") or response.get("reasoning_content") or ""
    if not content:
        raise ValueError("模型未返回有效分析结果")
    parser = RepairJSONParser()
    return await parser.invoke(content, default_value={})


def build_platform_search_links(query: str) -> List[Dict[str, str]]:
    encoded = quote(query)
    templates = [
        ("YouTube", "▶️", f"https://www.youtube.com/results?search_query={encoded}", "官方/免费公开"),
        ("哔哩哔哩", "📺", f"https://search.bilibili.com/all?keyword={encoded}", "正版/限免"),
        ("腾讯视频", "🎬", f"https://v.qq.com/x/search/?q={encoded}", "正版/限免"),
        ("优酷", "🎬", f"https://so.youku.com/search_video/q_{encoded}", "正版/限免"),
        ("爱奇艺", "🎬", f"https://so.iqiyi.com/so/q_{encoded}", "正版/限免"),
        ("芒果TV", "🎬", f"https://so.mgtv.com/so?k={encoded}", "正版/限免"),
        ("央视网", "📡", f"https://search.cctv.com/search.php?qtext={encoded}", "官方免费"),
    ]
    return [
        {
            "platform": name,
            "icon": icon,
            "url": url,
            "quality": "以平台页面为准",
            "condition": condition,
            "trust_score": 0.95,
        }
        for name, icon, url, condition in templates
    ]
