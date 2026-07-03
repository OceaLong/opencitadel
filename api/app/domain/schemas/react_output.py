#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Strict Pydantic schemas for ReAct structured outputs."""
import json
from typing import Any, List

from pydantic import BaseModel, Field, field_validator


def _coerce_result_to_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(f"{key}: {item}" for key, item in value.items())
    return json.dumps(value, ensure_ascii=False)


class ReactStepSchema(BaseModel):
    success: bool
    result: str = ""
    attachments: List[str] = Field(default_factory=list)

    model_config = {"extra": "ignore"}

    @field_validator("result", mode="before")
    @classmethod
    def coerce_result(cls, value: Any) -> str:
        return _coerce_result_to_str(value)


class ReactSummarySchema(BaseModel):
    message: str
    attachments: List[str] = Field(default_factory=list)

    model_config = {"extra": "ignore"}
