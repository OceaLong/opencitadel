#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Strict Pydantic schemas for planner structured outputs."""
from typing import List, Optional

from pydantic import BaseModel, Field

from app.domain.models.plan import ExecutionStatus


class PlannerStepSchema(BaseModel):
    id: Optional[str] = None
    description: str = Field(min_length=1)
    status: ExecutionStatus = ExecutionStatus.PENDING


class PlannerPlanSchema(BaseModel):
    title: str = Field(min_length=1)
    goal: str = ""
    language: str = "zh"
    steps: List[PlannerStepSchema] = Field(min_length=1)
    status: ExecutionStatus = ExecutionStatus.PENDING

    model_config = {"extra": "ignore"}


class PlannerUpdateSchema(BaseModel):
    """更新计划的结构化输出，更新时仅重新规划未完成步骤，因此只需要校验 steps。"""
    steps: List[PlannerStepSchema] = Field(min_length=1)

    model_config = {"extra": "ignore"}
