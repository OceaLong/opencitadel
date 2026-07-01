#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Structured output validation helpers.

RepairJSONParser handles syntax-level JSON repair locally. This module handles
semantic validation against Pydantic models after JSON syntax has been repaired.
"""
from __future__ import annotations

from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from app.domain.external.json_parser import JSONParser
from app.domain.services.agents.retry_budget import LLMRetryBudget

T = TypeVar("T", bound=BaseModel)


class StructuredParseError(ValueError):
    """Raised when a syntactically valid JSON payload fails schema validation."""


async def parse_structured_output(
        text: str,
        model_class: Type[T],
        json_parser: JSONParser,
        *,
        retry_budget: LLMRetryBudget | None = None,
) -> T:
    try:
        parsed = await json_parser.invoke(text)
        return model_class.model_validate(parsed)
    except ValidationError as exc:
        if retry_budget is not None:
            retry_budget.consume("structured_validation_retry")
        raise StructuredParseError(str(exc)) from exc

