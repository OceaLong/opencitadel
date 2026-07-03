#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Strict Pydantic schemas for ReAct structured outputs."""
from typing import List

from pydantic import BaseModel, Field


class ReactStepSchema(BaseModel):
    success: bool
    result: str = ""
    attachments: List[str] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


class ReactSummarySchema(BaseModel):
    message: str
    attachments: List[str] = Field(default_factory=list)

    model_config = {"extra": "ignore"}
