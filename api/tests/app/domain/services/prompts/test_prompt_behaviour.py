#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Behavioural assertions for prompt composition and schemas."""
import pytest
from pydantic import ValidationError

from app.domain.models.app_config import SandboxRuntimeConfig
from app.domain.models.skill import Skill
from app.domain.schemas.react_output import ReactStepSchema, ReactSummarySchema
from app.domain.services.prompts.loader import compose_system_prompt, load_prompts, render_sandbox_environment
from app.domain.services.skills.skill_import import parse_skill_md
from app.domain.services.skills.skill_loader import render_active, render_metadata

_DEFAULT_SANDBOX = SandboxRuntimeConfig()


def _compose(prompts, extra="", writing_style="prose"):
    return compose_system_prompt(
        prompts,
        extra,
        sandbox_runtime=_DEFAULT_SANDBOX,
        writing_style=writing_style,
    )


def test_system_prompt_contains_tool_discipline():
    for locale in ("en", "zh"):
        prompts = load_prompts(locale)
        composed = _compose(prompts)
        assert "<tool_use_discipline>" in composed
        assert "{file_rules}" not in composed
        assert "{writing_rules}" not in composed


def test_adaptive_style_allows_lists_in_zh():
    prompts = load_prompts("zh")
    composed = _compose(prompts, writing_style="adaptive")
    assert "严禁使用列表格式" not in composed


def test_prose_writing_rules_require_file_delivery_for_long_form():
    for locale in ("en", "zh"):
        prompts = load_prompts(locale)
        composed = _compose(prompts)
        assert "attachments" in composed


def test_react_prompts_require_file_delivery_for_long_form():
    for locale in ("en", "zh"):
        prompts = load_prompts(locale)
        assert "1500" in prompts.react.EXECUTION_PROMPT
        assert "attachments" in prompts.react.SUMMARIZE_PROMPT
        assert "write_file" in prompts.react.SUMMARIZE_PROMPT


def test_subagent_skill_prompt_injected_once():
    skill_prompt = "Follow the skill playbook exactly."
    prompts = load_prompts("en")
    system_prompt = _compose(prompts, prompts.internal.SUBAGENT_SYSTEM_PROMPT)
    full_content = system_prompt + f"\n\n--- Skill Instructions ---\n{skill_prompt}"
    assert full_content.count("--- Skill Instructions ---") == 1
    assert full_content.count(skill_prompt) == 1


def test_react_schemas_parse_minimal_payload():
    step = ReactStepSchema.model_validate({"success": True, "result": "done"})
    assert step.success is True
    summary = ReactSummarySchema.model_validate({"message": "ok", "attachments": []})
    assert summary.message == "ok"


def test_react_step_schema_requires_success():
    with pytest.raises(ValidationError):
        ReactStepSchema.model_validate({"result": "x"})


def test_react_step_schema_coerces_dict_result():
    step = ReactStepSchema.model_validate({
        "success": True,
        "result": {"current_time": "2026年7月5日 星期日"},
    })
    assert step.result == "current_time: 2026年7月5日 星期日"


def test_react_step_schema_coerces_list_result():
    step = ReactStepSchema.model_validate({
        "success": True,
        "result": ["line one", "line two"],
    })
    assert step.result == '["line one", "line two"]'


def test_skill_md_import():
    content = """---
name: Demo Skill
description: A demo
---
Do the demo task."""
    skill = parse_skill_md(content, slug="demo")
    assert skill.name == "Demo Skill"
    assert skill.source_format == "claude_md"
    assert "Do the demo task" in skill.body


def test_skill_loader_render_active():
    skill = Skill(
        name="Test",
        system_prompt="Be helpful",
        body="Extra body",
        examples=["ex1"],
    )
    assert "Be helpful" in render_active(skill)
    assert "Extra body" in render_active(skill)
    assert "Test" in render_metadata(skill)


def test_sandbox_render_custom_node():
    cfg = SandboxRuntimeConfig(node_version="99.0")
    text = render_sandbox_environment(cfg, "en")
    assert "99.0" in text
