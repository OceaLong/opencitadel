#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Structured output validation helpers.

RepairJSONParser handles syntax-level JSON repair locally. This module handles
semantic validation against Pydantic models after JSON syntax has been repaired.
"""
from __future__ import annotations

from typing import Any, Type, TypeVar

from pydantic import BaseModel, ValidationError

from app.domain.external.json_parser import JSONParser
from app.domain.schemas.react_output import ReactStepSchema, ReactSummarySchema

T = TypeVar("T", bound=BaseModel)


class StructuredParseError(ValueError):
    """Raised when a syntactically valid JSON payload fails schema validation."""


def _coerce_plain_text_to_schema(
        parsed: Any,
        *,
        model_class: Type[BaseModel],
) -> dict[str, Any] | None:
    if not isinstance(parsed, str):
        return None
    text = parsed.strip()
    if not text:
        return None
    if model_class is ReactStepSchema:
        return {"success": True, "result": text, "attachments": []}
    if model_class is ReactSummarySchema:
        return {"message": text, "attachments": []}
    return None


def _ensure_mapping(
        parsed: Any,
        *,
        model_class: Type[BaseModel],
        allow_plain_text_coercion: bool = False,
) -> dict[str, Any]:
    if isinstance(parsed, dict):
        return parsed
    if allow_plain_text_coercion:
        coerced = _coerce_plain_text_to_schema(parsed, model_class=model_class)
        if coerced is not None:
            return coerced
    raise StructuredParseError(
        f"Expected a JSON object for {model_class.__name__}, got {type(parsed).__name__}"
    )


async def parse_structured_output(
        text: str,
        model_class: Type[T],
        json_parser: JSONParser,
        *,
        allow_plain_text_coercion: bool = False,
) -> T:
    try:
        parsed = await json_parser.invoke(text)
    except ValueError as exc:
        raise StructuredParseError(str(exc)) from exc

    try:
        payload = _ensure_mapping(
            parsed,
            model_class=model_class,
            allow_plain_text_coercion=allow_plain_text_coercion,
        )
        return model_class.model_validate(payload)
    except ValidationError as exc:
        raise StructuredParseError(str(exc)) from exc
