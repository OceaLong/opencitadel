#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
import logging
from typing import Any, Dict, Optional

from app.domain.external.llm import LLM
from app.infrastructure.external.json_parser.repair_json_parser import RepairJSONParser

logger = logging.getLogger(__name__)

FORTUNE_MODES = {"fortune", "lottery", "divination", "astrology"}

MODE_LABELS = {
    "fortune": "运势预测",
    "lottery": "抽签",
    "divination": "算命",
    "astrology": "星盘推演",
}

FORTUNE_PROMPT = """你是娱乐向运势解读助手。请根据用户输入生成轻松、积极、富有画面感的中文预测内容。
重要约束：
1. 仅作娱乐参考，不可给出医疗、法律、投资等确定性建议。
2. 语气温暖、诗意，避免恐吓或绝对化表述。
3. 仅返回 JSON，不要 markdown 代码块。

返回格式：
{{
  "title": "结果标题，8-16字",
  "summary": "一句话总览，20-40字",
  "sections": [
    {{"heading": "分段标题", "content": "分段解读，50-120字"}}
  ],
  "lucky_items": {{
    "color": "幸运色",
    "number": "幸运数字",
    "keyword": "关键词",
    "element": "五行/元素（可选）"
  }},
  "disclaimer": "本结果仅供娱乐参考，请理性看待。"
}}

预测类型：{mode_label}
用户问题：{question}
用户资料：{profile}
"""

FALLBACK_RESULTS: Dict[str, Dict[str, Any]] = {
    "fortune": {
        "title": "云开见月明",
        "summary": "近期运势平稳向上，适合放慢脚步、整理心绪。",
        "sections": [
            {"heading": "整体运势", "content": "你会迎来一段温和上升期，小事顺心，适合规划与沉淀。"},
            {"heading": "行动建议", "content": "保持耐心，把精力放在真正重要的事上，好运会自然靠近。"},
        ],
        "lucky_items": {"color": "月白", "number": "7", "keyword": "从容", "element": "水"},
        "disclaimer": "本结果仅供娱乐参考，请理性看待。",
    },
    "lottery": {
        "title": "上上签 · 静水流深",
        "summary": "签文示吉，守正出奇，宜稳中求进。",
        "sections": [
            {"heading": "签文", "content": "静水深流藏智慧，守得云开见月明。莫因一时困顿而失方寸。"},
            {"heading": "指引", "content": "近期宜少言多思，把精力用在准备与积累上，机缘将至。"},
        ],
        "lucky_items": {"color": "金色", "number": "8", "keyword": "守正", "element": "金"},
        "disclaimer": "本结果仅供娱乐参考，请理性看待。",
    },
    "divination": {
        "title": "卦象示吉",
        "summary": "卦象温和，主贵人相助、事缓则圆。",
        "sections": [
            {"heading": "卦辞", "content": "当前局势宜守不宜攻，以柔克刚，可化险为夷。"},
            {"heading": "指引", "content": "多倾听他人意见，保持谦逊，自有转机出现。"},
        ],
        "lucky_items": {"color": "青色", "number": "3", "keyword": "顺势", "element": "木"},
        "disclaimer": "本结果仅供娱乐参考，请理性看待。",
    },
    "astrology": {
        "title": "星盘微光",
        "summary": "星象显示你正处于自我觉察与成长的阶段。",
        "sections": [
            {"heading": "性格特质", "content": "你兼具感性与理性，善于在变化中找到自己的节奏。"},
            {"heading": "近期指引", "content": "适合学习新技能、拓展视野，人际关系会有温暖互动。"},
        ],
        "lucky_items": {"color": "紫色", "number": "9", "keyword": "觉醒", "element": "风"},
        "disclaimer": "本结果仅供娱乐参考，请理性看待。",
    },
}


class FortuneService:
    def __init__(self) -> None:
        self._json_parser = RepairJSONParser()

    async def generate(
            self,
            llm: LLM,
            *,
            mode: str,
            question: str,
            input_profile: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        mode = (mode or "fortune").strip().lower()
        if mode not in FORTUNE_MODES:
            raise ValueError(f"不支持的预测类型，可选：{', '.join(sorted(FORTUNE_MODES))}")

        question = (question or "").strip()
        if not question:
            raise ValueError("请输入你想预测的问题")

        profile = input_profile or {}
        prompt = FORTUNE_PROMPT.format(
            mode_label=MODE_LABELS.get(mode, mode),
            question=question,
            profile=json.dumps(profile, ensure_ascii=False),
        )

        try:
            response = await llm.invoke([{"role": "user", "content": prompt}])
            content = response.get("content") or response.get("reasoning_content") or ""
            parsed = await self._json_parser.invoke(content, default_value={})
            result = self._normalize_result(parsed, mode)
        except Exception as exc:
            logger.info("运势预测 LLM 降级为模板结果: %s", exc)
            result = dict(FALLBACK_RESULTS.get(mode, FALLBACK_RESULTS["fortune"]))

        result["mode"] = mode
        return result

    def _normalize_result(self, parsed: Any, mode: str) -> Dict[str, Any]:
        if not isinstance(parsed, dict):
            parsed = {}

        fallback = FALLBACK_RESULTS.get(mode, FALLBACK_RESULTS["fortune"])
        sections = parsed.get("sections")
        if not isinstance(sections, list) or not sections:
            sections = fallback["sections"]
        else:
            sections = [
                {
                    "heading": str(item.get("heading") or "解读"),
                    "content": str(item.get("content") or ""),
                }
                for item in sections
                if isinstance(item, dict)
            ][:5]

        lucky = parsed.get("lucky_items")
        if not isinstance(lucky, dict):
            lucky = fallback["lucky_items"]

        return {
            "title": str(parsed.get("title") or fallback["title"]).strip(),
            "summary": str(parsed.get("summary") or fallback["summary"]).strip(),
            "sections": sections,
            "lucky_items": {
                "color": str(lucky.get("color") or fallback["lucky_items"]["color"]),
                "number": str(lucky.get("number") or fallback["lucky_items"]["number"]),
                "keyword": str(lucky.get("keyword") or fallback["lucky_items"]["keyword"]),
                "element": str(lucky.get("element") or fallback["lucky_items"].get("element", "")),
            },
            "disclaimer": str(parsed.get("disclaimer") or fallback["disclaimer"]).strip(),
        }
