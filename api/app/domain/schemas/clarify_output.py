#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Strict Pydantic schemas for clarify structured outputs."""
from typing import List, Optional

from pydantic import BaseModel, Field


class ClarifyOptionSchema(BaseModel):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)


class ClarifyQuestionSchema(BaseModel):
    id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    options: List[ClarifyOptionSchema] = Field(default_factory=list)
    allow_multiple: bool = False
    allow_custom: bool = True


class ClarifyOutputSchema(BaseModel):
    needs_clarification: bool = False
    questions: List[ClarifyQuestionSchema] = Field(default_factory=list)
    brief: Optional[str] = None
    title: Optional[str] = None

    model_config = {"extra": "ignore"}
