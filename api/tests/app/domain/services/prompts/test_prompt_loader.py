#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tests for locale-aware prompt loading and composition."""
from app.domain.models.app_config import SandboxRuntimeConfig
from app.domain.services.prompts.loader import (
    compose_system_prompt,
    detect_locale_from_text,
    load_prompts,
    render_sandbox_environment,
    resolve_writing_style,
)

_DEFAULT_SANDBOX = SandboxRuntimeConfig()


def _compose(prompts, extra="", writing_style="prose"):
    return compose_system_prompt(
        prompts,
        extra,
        sandbox_runtime=_DEFAULT_SANDBOX,
        writing_style=writing_style,
    )


def test_load_prompts_defaults_to_english():
    prompts = load_prompts()
    assert prompts.locale == "en"
    assert "OpenCitadel" in prompts.system.SYSTEM_PROMPT


def test_load_prompts_uses_chinese_for_cjk_text():
    prompts = load_prompts(detect_locale_from_text("请帮我分析这份文档"))
    assert prompts.locale == "zh"
    composed = _compose(prompts)
    assert "OpenCitadel" in composed
    assert "默认工作语言" in composed or "中文" in composed


def test_detect_locale_from_text():
    assert detect_locale_from_text("hello world") == "en"
    assert detect_locale_from_text("你好") == "zh"


def test_compose_system_prompt_injects_sandbox_environment():
    prompts = load_prompts("en")
    composed = _compose(prompts)
    assert "{sandbox_environment}" not in composed
    assert "Node.js" in composed
    cfg = SandboxRuntimeConfig(node_version="24.x-test")
    block = render_sandbox_environment(cfg, "en")
    assert "24.x-test" in block


def test_compose_adaptive_writing_rules():
    prompts = load_prompts("en")
    composed = _compose(prompts, writing_style="adaptive")
    assert "never use list formatting" not in composed.lower()
    assert "<writing_rules>" in composed


def test_resolve_writing_style_skill_override():
    assert resolve_writing_style("adaptive", False, "prose") == "adaptive"
    assert resolve_writing_style(None, override_base_rules=True, global_default="prose") == "adaptive"
    assert resolve_writing_style(None, False, "prose") == "prose"


def test_internal_prompts_localized():
    zh = load_prompts("zh")
    en = load_prompts("en")
    assert "历史" in zh.internal.MEMORY_SUMMARY_PROMPT
    assert "History" in en.internal.MEMORY_SUMMARY_PROMPT


def test_flow_prompts_exist_for_both_locales():
    for locale in ("en", "zh"):
        prompts = load_prompts(locale)
        assert prompts.flows.CODE_ASK_PROMPT.strip()
        assert prompts.flows.DOC_QA_PROMPT.strip()
        assert prompts.flows.HYBRID_ASK_PROMPT.strip()


def test_ask_flow_compose_has_no_placeholder_residue():
    placeholders = ("{sandbox_environment}", "{file_rules}", "{writing_rules}", "{tool_use_discipline}")
    for locale in ("en", "zh"):
        prompts = load_prompts(locale)
        for extra in (
            prompts.flows.CODE_ASK_PROMPT,
            prompts.flows.DOC_QA_PROMPT,
            prompts.flows.HYBRID_ASK_PROMPT,
        ):
            composed = _compose(prompts, extra)
            for token in placeholders:
                assert token not in composed
