#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tests for locale-aware prompt loading."""
from app.domain.services.prompts.loader import detect_locale_from_text, load_prompts


def test_load_prompts_defaults_to_english():
    prompts = load_prompts()
    assert "OpenCitadel" in prompts.system.SYSTEM_PROMPT


def test_load_prompts_uses_chinese_for_cjk_text():
    prompts = load_prompts(detect_locale_from_text("请帮我分析这份文档"))
    assert "OpenCitadel" in prompts.system.SYSTEM_PROMPT
    assert "默认工作语言" in prompts.system.SYSTEM_PROMPT or "中文" in prompts.system.SYSTEM_PROMPT


def test_detect_locale_from_text():
    assert detect_locale_from_text("hello world") == "en"
    assert detect_locale_from_text("你好") == "zh"
