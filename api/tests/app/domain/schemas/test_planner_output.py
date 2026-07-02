#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.domain.schemas.planner_output import PlannerPlanSchema
from app.domain.services.prompts.planner import CREATE_PLAN_PROMPT


def test_planner_plan_schema_requires_steps():
    validated = PlannerPlanSchema.model_validate({
        "title": "Research task",
        "goal": "Find info",
        "steps": [{"description": "Search web"}],
    })
    assert validated.title == "Research task"
    assert len(validated.steps) == 1


def test_planner_plan_schema_rejects_empty_steps():
    with pytest.raises(Exception):
        PlannerPlanSchema.model_validate({
            "title": "Bad",
            "steps": [],
        })


def test_create_plan_prompt_matches_non_empty_steps_schema():
    assert 'empty "steps"' in CREATE_PLAN_PROMPT or "empty steps" in CREATE_PLAN_PROMPT.lower()
    assert "steps" in CREATE_PLAN_PROMPT


def test_planner_plan_schema_ignores_legacy_message_field():
    validated = PlannerPlanSchema.model_validate({
        "title": "Research task",
        "goal": "Find info",
        "message": "should be ignored",
        "steps": [{"description": "Search web"}],
    })
    assert validated.title == "Research task"
    assert "message" not in validated.model_dump()
