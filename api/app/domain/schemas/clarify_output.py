#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Strict Pydantic schemas for clarify structured outputs."""
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

MIN_CLARIFY_BRIEF_LENGTH = 20
MIN_CLARIFY_OPTIONS = 2


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
    needs_clarification: bool
    questions: List[ClarifyQuestionSchema] = Field(default_factory=list)
    brief: Optional[str] = None
    title: Optional[str] = None

    model_config = {"extra": "ignore"}

    @model_validator(mode="after")
    def validate_clarify_consistency(self) -> "ClarifyOutputSchema":
        if self.needs_clarification:
            if not self.questions:
                raise ValueError(
                    "needs_clarification=true requires at least one question"
                )
            for question in self.questions:
                if len(question.options) < MIN_CLARIFY_OPTIONS:
                    raise ValueError(
                        f"question '{question.id}' must include at least "
                        f"{MIN_CLARIFY_OPTIONS} options"
                    )
            return self

        brief = (self.brief or "").strip()
        if len(brief) < MIN_CLARIFY_BRIEF_LENGTH:
            raise ValueError(
                f"needs_clarification=false requires brief with at least "
                f"{MIN_CLARIFY_BRIEF_LENGTH} characters"
            )
        self.brief = brief
        return self
