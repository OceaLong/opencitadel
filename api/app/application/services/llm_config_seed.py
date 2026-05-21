#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Any, Dict, Optional

from app.domain.models.app_config import LLMConfig


def is_seedable_llm_config_raw(raw: Optional[Dict[str, Any]]) -> bool:
    """判断 config.yaml 是否显式提供了可种子化的 LLM 配置。"""
    if not raw or not isinstance(raw, dict):
        return False
    llm = raw.get("llm_config")
    if not isinstance(llm, dict):
        return False
    base_url = str(llm.get("base_url") or "").strip()
    model_name = str(llm.get("model_name") or "").strip()
    if not base_url or not model_name:
        return False
    api_key = str(llm.get("api_key") or "").strip()
    lowered = base_url.lower()
    if "ollama" in lowered or ":11434" in lowered:
        return True
    return bool(api_key)


def is_seedable_llm_config(llm_config: Optional[LLMConfig]) -> bool:
    if llm_config is None:
        return False
    base_url = str(llm_config.base_url).strip()
    model_name = llm_config.model_name.strip()
    if not base_url or not model_name:
        return False
    lowered = base_url.lower()
    if "ollama" in lowered or ":11434" in lowered:
        return True
    return bool(llm_config.api_key.strip())
