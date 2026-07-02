#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Any

MARKETPLACE_APPS: list[dict[str, Any]] = [
    {
        "id": "nutrition-analysis",
        "name": "AI营养分析",
        "description": "拍照识别餐食营养，减脂/增肌红绿灯评估",
        "icon": "🥗",
        "category": "健康",
        "tags": ["视觉识别", "营养", "健身", "减脂"],
        "featured": True,
        "accent": "emerald",
        "needs_vision": True,
        "examples": ["分析这顿饭适不适合减脂", "看看这张餐食蛋白够不够"],
    },
    {
        "id": "consumption-calculator",
        "name": "消耗计算器",
        "description": "识别包装净含量，计算可食用次数",
        "icon": "📦",
        "category": "生活",
        "tags": ["OCR", "计算", "包装识别", "生活效率"],
        "featured": False,
        "accent": "amber",
        "needs_vision": True,
        "examples": ["这包 1.2kg 每次 50g 能吃几次", "识别包装净含量并计算消耗"],
    },
    {
        "id": "smart-translation",
        "name": "智能翻译",
        "description": "自动识别语种，按正式/口语/技术风格翻译",
        "icon": "🌐",
        "category": "办公",
        "tags": ["翻译", "润色", "多语言", "技术文档"],
        "featured": True,
        "accent": "indigo",
        "needs_vision": False,
        "examples": ["把这段英文翻译成正式中文", "翻译截图里的文字为英文"],
    },
    {
        "id": "prompt-lab",
        "name": "提示词工坊",
        "description": "把粗略想法改写为可复用的高质量提示词",
        "icon": "✨",
        "category": "效率",
        "tags": ["提示词", "工作流", "创作", "效率"],
        "featured": False,
        "accent": "rose",
        "needs_vision": False,
        "examples": ["帮我写一个数据分析 Agent 提示词", "优化这段客服回复 prompt"],
    },
    {
        "id": "qr-generator",
        "name": "二维码生成器",
        "description": "将文本或链接快速生成可下载的二维码图片",
        "icon": "📱",
        "category": "效率",
        "tags": ["二维码", "链接", "分享", "下载"],
        "featured": False,
        "accent": "sky",
        "needs_vision": False,
        "examples": ["生成 https://example.com 的二维码", "把这段文字做成二维码"],
    },
    {
        "id": "dev-toolbox",
        "name": "开发者工具箱",
        "description": "JSON 格式化、Base64 与 URL 编解码一站式处理",
        "icon": "🛠️",
        "category": "效率",
        "tags": ["JSON", "Base64", "URL", "格式化"],
        "featured": False,
        "accent": "indigo",
        "needs_vision": False,
        "examples": ["格式化这段 JSON", "把文本 Base64 编码", "URL 解码这段参数"],
    },
    {
        "id": "secret-generator",
        "name": "密码 & UUID 生成器",
        "description": "生成可配置强度的密码与批量 UUID",
        "icon": "🔐",
        "category": "效率",
        "tags": ["密码", "UUID", "安全", "随机"],
        "featured": False,
        "accent": "rose",
        "needs_vision": False,
        "examples": ["生成 16 位强密码", "批量生成 5 个 UUID"],
    },
    {
        "id": "document-converter",
        "name": "文档格式转换",
        "description": "md/txt 转 PDF、PDF 转 Word、常用文档格式互转",
        "icon": "📑",
        "category": "办公",
        "tags": ["PDF", "Word", "Markdown", "格式转换"],
        "featured": True,
        "accent": "sky",
        "needs_vision": False,
        "examples": ["把这份 PDF 转成 Word", "md 转 pdf", "pdf 转 markdown"],
    },
    {
        "id": "watermark-tool",
        "name": "水印工具",
        "description": "图片/PDF 加水印，图片 AI 去水印与 PDF 去水印",
        "icon": "💧",
        "category": "办公",
        "tags": ["水印", "去水印", "PDF", "图片处理"],
        "featured": False,
        "accent": "indigo",
        "needs_vision": True,
        "examples": ["给 PDF 加上机密水印", "去掉图片右下角水印", "批量平铺文字水印"],
    },
]

APP_IDS = {app["id"] for app in MARKETPLACE_APPS}

# Keep model_dependency explicit until backend/frontend marketplace metadata is centralized.
_APP_MODEL_DEPENDENCY: dict[str, str] = {
    "nutrition-analysis": "required",
    "consumption-calculator": "required",
    "smart-translation": "required",
    "prompt-lab": "required",
    "qr-generator": "none",
    "dev-toolbox": "none",
    "secret-generator": "none",
    "document-converter": "none",
    "watermark-tool": "optional",
}


def enrich_marketplace_app(app: dict) -> dict:
    enriched = dict(app)
    enriched.setdefault(
        "model_dependency",
        _APP_MODEL_DEPENDENCY.get(app["id"], "optional"),
    )
    return enriched


def list_marketplace_apps() -> list[dict]:
    return [enrich_marketplace_app(app) for app in MARKETPLACE_APPS]


def app_id_enum() -> str:
    return " | ".join(app["id"] for app in MARKETPLACE_APPS)


def examples_for(app_id: str) -> list[str]:
    for app in MARKETPLACE_APPS:
        if app["id"] == app_id:
            return list(app.get("examples", []))
    return []


def build_route_prompt() -> str:
    return f"""你是应用市场的智能分发助手。根据用户输入，从候选应用中选择最合适的一个，并抽取可直接预填的参数。
候选应用：
{{apps}}

仅返回 JSON：
{{
  "app_id": "{app_id_enum()}",
  "confidence": 0.0,
  "reason": "一句中文理由",
  "params": {{}},
  "suggestions": ["可选的下一步建议"]
}}

抽参规则：
- consumption-calculator: params.serving_grams 和 params.total_grams 可从文本提取。
- smart-translation: params.text / params.target_language / params.style。
- nutrition-analysis: params.goal 可为 cut/bulk/maintain。
- qr-generator: params.text = 要编码的文本或链接。
- dev-toolbox: params.text = 待处理的 JSON/Base64/URL 文本。
- secret-generator: params.length = 密码长度（数字）。
- document-converter: params.target_format 可为 pdf/docx/md/txt。
- watermark-tool: params.mode 可为 add/remove；params.text = 水印文字。"""
