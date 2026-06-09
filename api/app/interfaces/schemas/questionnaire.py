#!/usr/bin/env python
# -*- coding: utf-8 -*-
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class QuestionOptionSchema(BaseModel):
    id: str
    text: str


class QuestionSchema(BaseModel):
    id: str
    type: str = "single"
    text: str
    options: List[QuestionOptionSchema] = Field(default_factory=list)
    required: bool = True
    rating_max: int = 5


class CreateQuestionnaireRequest(BaseModel):
    title: str
    description: str = ""
    questions: List[Dict[str, Any]] = Field(default_factory=list)


class UpdateQuestionnaireRequest(BaseModel):
    manage_token: str
    title: Optional[str] = None
    description: Optional[str] = None
    questions: Optional[List[Dict[str, Any]]] = None


class PublishQuestionnaireRequest(BaseModel):
    manage_token: str


class SubmitResponseRequest(BaseModel):
    answers: Dict[str, Any]
    respondent_name: Optional[str] = None


class QuestionnaireResponseSchema(BaseModel):
    id: str
    title: str
    description: str = ""
    questions: List[Dict[str, Any]] = Field(default_factory=list)
    status: str
    slug: str
    manage_token: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class SubmitResponseResultSchema(BaseModel):
    id: str
    message: str


class QuestionnaireStatsSchema(BaseModel):
    questionnaire_id: str
    title: str
    status: str
    slug: str
    total_responses: int
    per_question: Dict[str, Any] = Field(default_factory=dict)
