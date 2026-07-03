#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio

import pytest

from app.domain.schemas.react_output import ReactStepSchema, ReactSummarySchema
from app.domain.services.agents.structured_parse import StructuredParseError, parse_structured_output


class _ParserReturnsPlainText:
    async def invoke(self, text: str):
        return "任务已完成"


class _ParserReturnsEmptyString:
    async def invoke(self, text: str):
        return ""


class _ParserReturnsValidDict:
    async def invoke(self, text: str):
        return {"success": True, "result": "ok", "attachments": []}


class _ParserRaisesValueError:
    async def invoke(self, text: str):
        raise ValueError("json文本为空，且无默认值")


async def _test_rejects_non_dict_parsed_value():
    with pytest.raises(StructuredParseError, match="Expected a JSON object"):
        await parse_structured_output("", ReactStepSchema, _ParserReturnsEmptyString())


async def _test_wraps_json_parser_value_error():
    with pytest.raises(StructuredParseError, match="json文本为空"):
        await parse_structured_output("", ReactStepSchema, _ParserRaisesValueError())


async def _test_accepts_valid_dict():
    parsed = await parse_structured_output("ignored", ReactStepSchema, _ParserReturnsValidDict())
    assert parsed.success is True
    assert parsed.result == "ok"


async def _test_coerces_plain_text_for_step_schema():
    parsed = await parse_structured_output(
        "ignored",
        ReactStepSchema,
        _ParserReturnsPlainText(),
        allow_plain_text_coercion=True,
    )
    assert parsed.success is True
    assert parsed.result == "任务已完成"
    assert parsed.attachments == []


async def _test_coerces_plain_text_for_summary_schema():
    parsed = await parse_structured_output(
        "ignored",
        ReactSummarySchema,
        _ParserReturnsPlainText(),
        allow_plain_text_coercion=True,
    )
    assert parsed.message == "任务已完成"


async def _test_plain_text_coercion_disabled_by_default():
    with pytest.raises(StructuredParseError, match="Expected a JSON object"):
        await parse_structured_output("", ReactStepSchema, _ParserReturnsPlainText())


def test_rejects_non_dict_parsed_value():
    asyncio.run(_test_rejects_non_dict_parsed_value())


def test_wraps_json_parser_value_error():
    asyncio.run(_test_wraps_json_parser_value_error())


def test_accepts_valid_dict():
    asyncio.run(_test_accepts_valid_dict())


def test_coerces_plain_text_for_step_schema():
    asyncio.run(_test_coerces_plain_text_for_step_schema())


def test_coerces_plain_text_for_summary_schema():
    asyncio.run(_test_coerces_plain_text_for_summary_schema())


def test_plain_text_coercion_disabled_by_default():
    asyncio.run(_test_plain_text_coercion_disabled_by_default())
