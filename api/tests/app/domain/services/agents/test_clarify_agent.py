#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
import json

import pytest
from pydantic import ValidationError

from app.domain.models.event import ClarifyEvent, MessageEvent
from app.domain.models.message import Message
from app.domain.schemas.clarify_output import ClarifyOutputSchema, MIN_CLARIFY_BRIEF_LENGTH
from app.domain.services.agents.clarify import ClarifyAgent
from app.domain.services.agents.retry_budget import LLMRetryBudget
from app.domain.services.prompts.loader import compose_clarify_system_prompt, load_prompts


class DummyJsonParser:
    async def invoke(self, text: str):
        return json.loads(text)


def test_clarify_agent_yields_clarify_event_when_questions_needed():
    agent = ClarifyAgent.__new__(ClarifyAgent)
    agent._json_parser = DummyJsonParser()
    agent.last_brief = None
    agent._skill_prompt = ""
    agent._long_term_memory_block = ""
    agent._system_prompt = ""
    agent._retry_budget = LLMRetryBudget.create(max_calls=5, max_seconds=120.0)

    async def fake_invoke(*args, **kwargs):
        yield MessageEvent(
            message=json.dumps({
                "needs_clarification": True,
                "title": "需要确认",
                "questions": [
                    {
                        "id": "scope",
                        "prompt": "选择范围",
                        "options": [
                            {"id": "api", "label": "API"},
                            {"id": "ui", "label": "UI"},
                        ],
                        "allow_multiple": False,
                        "allow_custom": True,
                    }
                ],
            })
        )

    agent.invoke = fake_invoke

    async def collect():
        return [event async for event in agent.analyze(Message(message="实现功能"))]

    events = asyncio.run(collect())

    assert len(events) == 1
    assert isinstance(events[0], ClarifyEvent)
    assert events[0].questions[0].prompt == "选择范围"


def test_clarify_agent_records_brief_when_no_questions_needed():
    agent = ClarifyAgent.__new__(ClarifyAgent)
    agent._json_parser = DummyJsonParser()
    agent.last_brief = None
    agent._skill_prompt = ""
    agent._long_term_memory_block = ""
    agent._system_prompt = ""
    agent._retry_budget = LLMRetryBudget.create(max_calls=5, max_seconds=120.0)
    brief = "用户希望实现用户登录功能，包含邮箱注册、密码重置与会话保持，交付为后端 API 与基础集成测试。"

    async def fake_invoke(*args, **kwargs):
        yield MessageEvent(
            message=json.dumps({
                "needs_clarification": False,
                "questions": [],
                "brief": brief,
            })
        )

    agent.invoke = fake_invoke

    async def collect():
        return [event async for event in agent.analyze(Message(message="明确需求"))]

    events = asyncio.run(collect())

    assert events == []
    assert agent.last_brief == brief


def test_clarify_output_schema_rejects_true_without_questions():
    with pytest.raises(ValidationError):
        ClarifyOutputSchema.model_validate({
            "needs_clarification": True,
            "questions": [],
        })


def test_clarify_output_schema_rejects_true_with_single_option():
    with pytest.raises(ValidationError):
        ClarifyOutputSchema.model_validate({
            "needs_clarification": True,
            "questions": [
                {
                    "id": "scope",
                    "prompt": "选择范围",
                    "options": [{"id": "api", "label": "API"}],
                    "allow_multiple": False,
                    "allow_custom": True,
                }
            ],
        })


def test_clarify_output_schema_rejects_false_without_brief():
    with pytest.raises(ValidationError):
        ClarifyOutputSchema.model_validate({
            "needs_clarification": False,
            "questions": [],
        })


def test_clarify_output_schema_rejects_false_with_short_brief():
    with pytest.raises(ValidationError):
        ClarifyOutputSchema.model_validate({
            "needs_clarification": False,
            "questions": [],
            "brief": "太短",
        })


def test_clarify_output_schema_accepts_valid_brief():
    brief = "x" * MIN_CLARIFY_BRIEF_LENGTH
    validated = ClarifyOutputSchema.model_validate({
        "needs_clarification": False,
        "questions": [],
        "brief": brief,
    })
    assert validated.brief == brief


def test_compose_clarify_system_prompt_excludes_execution_notes():
    prompts = load_prompts("zh")
    system_prompt = compose_clarify_system_prompt(prompts)
    assert "Clarify Agent" in system_prompt or "澄清" in system_prompt
    assert "role_override" in system_prompt
    assert "不要向用户交付待办事项列表" not in system_prompt
    assert "Do not deliver todo lists" not in system_prompt
    assert "<sandbox_environment>" not in system_prompt
