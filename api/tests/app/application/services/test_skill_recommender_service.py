#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tests for skill recommender service."""
import pytest

from app.application.services.skill_recommender_service import SkillRecommenderService
from app.domain.models.skill import Skill


class _FakeJsonParser:
    async def invoke(self, content: str):
        return content


class _FakeLLM:
    model_name = "test"

    async def invoke(self, messages, **kwargs):
        return {"content": '{"skill_id": "s1", "confidence": 0.9, "reason": "match"}'}


@pytest.mark.asyncio
async def test_recommend_selects_skill():
    skills = [
        Skill(id="s1", name="Coding", description="code", auto_recommend=True),
        Skill(id="s2", name="Writing", description="write", auto_recommend=True),
    ]
    service = SkillRecommenderService(_FakeLLM(), _FakeJsonParser(), confidence_threshold=0.5)
    result = await service.recommend("fix python bug", skills)
    assert result.skill_id == "s1"
    assert result.confidence >= 0.5


@pytest.mark.asyncio
async def test_recommend_below_threshold_returns_empty():
    class _LowLLM:
        model_name = "test"

        async def invoke(self, messages, **kwargs):
            return {"content": '{"skill_id": "s1", "confidence": 0.1, "reason": "weak"}'}

    skills = [Skill(id="s1", name="Coding", description="code", auto_recommend=True)]
    service = SkillRecommenderService(_LowLLM(), _FakeJsonParser(), confidence_threshold=0.55)
    result = await service.recommend("hello", skills)
    assert result.skill_id is None
