#!/usr/bin/env python
# -*- coding: utf-8 -*-
import pytest

from app.domain.schemas.planner_output import PlannerPlanSchema


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
