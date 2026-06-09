#!/usr/bin/env python
# -*- coding: utf-8 -*-
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class QuestionnaireStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    CLOSED = "closed"


class QuestionType(str, Enum):
    SINGLE = "single"
    MULTIPLE = "multiple"
    RATING = "rating"
    TEXT = "text"


@dataclass
class QuestionOption:
    id: str
    text: str


@dataclass
class Question:
    id: str
    type: QuestionType
    text: str
    options: List[QuestionOption] = field(default_factory=list)
    required: bool = True
    rating_max: int = 5


@dataclass
class Questionnaire:
    id: str
    title: str
    description: str
    questions: List[Dict[str, Any]]
    status: QuestionnaireStatus
    slug: str
    manage_token: str
    created_at: datetime
    updated_at: datetime


@dataclass
class QuestionnaireResponse:
    id: str
    questionnaire_id: str
    answers: Dict[str, Any]
    respondent_name: Optional[str]
    created_at: datetime
