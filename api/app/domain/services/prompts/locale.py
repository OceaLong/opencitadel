#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Prompt locale resolution helpers."""
from __future__ import annotations

import os
from typing import Optional

DEFAULT_PROMPT_LOCALE = "en"
SUPPORTED_PROMPT_LOCALES = frozenset({"en", "zh"})

_CHINESE_LANGUAGE_HINTS = frozenset({
    "zh",
    "zh-cn",
    "zh-tw",
    "zh-hk",
    "zh-hans",
    "zh-hant",
    "chinese",
    "中文",
    "汉语",
    "普通话",
    "简体",
    "繁体",
})


def normalize_prompt_locale(locale: Optional[str]) -> str:
    if not locale:
        return DEFAULT_PROMPT_LOCALE
    value = str(locale).strip().lower().replace("_", "-")
    if value.startswith("zh"):
        return "zh"
    if value in SUPPORTED_PROMPT_LOCALES:
        return value
    return DEFAULT_PROMPT_LOCALE


def is_chinese_working_language(working_language: Optional[str]) -> bool:
    if not working_language:
        return False
    value = str(working_language).strip().lower().replace("_", "-")
    if value.startswith("zh"):
        return True
    return value in _CHINESE_LANGUAGE_HINTS or "中文" in str(working_language)


def resolve_prompt_locale(
        working_language: Optional[str] = None,
        config_locale: Optional[str] = None,
) -> str:
    """Resolve prompt locale: default English; use zh for Chinese working language or config."""
    configured = config_locale or os.getenv("PROMPT_LOCALE")
    normalized_config = normalize_prompt_locale(configured)
    if normalized_config == "zh":
        return "zh"
    if is_chinese_working_language(working_language):
        return "zh"
    return DEFAULT_PROMPT_LOCALE
