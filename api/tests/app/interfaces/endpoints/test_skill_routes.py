#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Tests for skill recommendation endpoint guards."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.interfaces.endpoints.skill_routes import recommend_skill


@pytest.mark.asyncio
async def test_recommend_returns_empty_when_no_default_model():
    skill_service = AsyncMock()
    skill_service.list_skills = AsyncMock(return_value=[])
    llm_model_service = AsyncMock()
    llm_model_service.get_default_model = AsyncMock(return_value=None)
    json_parser = MagicMock()
    ctx = MagicMock()

    response = await recommend_skill(
        message="help me refactor",
        ctx=ctx,
        skill_service=skill_service,
        llm_model_service=llm_model_service,
        json_parser=json_parser,
    )

    assert response.data.skill_id is None
    assert response.data.confidence == 0.0
    llm_model_service.get_default_model.assert_awaited_once()
