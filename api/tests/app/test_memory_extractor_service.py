#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.application.services.memory_extractor_service import MemoryExtractorService
from app.domain.models.session import Session


async def _run_memory_extractor_uses_reasoning_content_fallback():
    session = Session(id="s1", title="t", events=[])
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.session.get_by_id = AsyncMock(return_value=session)
    uow.session.list_events = AsyncMock(return_value=[])
    uow.memory_entry.save = AsyncMock()

    llm = MagicMock()
    llm.invoke = AsyncMock(return_value={
        "content": "",
        "reasoning_content": '[{"title":"t","content":"c","tags":["a"]}]',
    })
    json_parser = MagicMock()
    json_parser.invoke = AsyncMock(return_value=[{"title": "t", "content": "c", "tags": ["a"]}])

    service = MemoryExtractorService(lambda: uow, llm, json_parser)
    entries = await service.extract_from_session("s1")
    assert len(entries) == 1
    assert entries[0].title == "t"


def test_memory_extractor_uses_reasoning_content_fallback():
    asyncio.run(_run_memory_extractor_uses_reasoning_content_fallback())


async def _run_memory_extractor_returns_empty_on_failure():
    session = Session(id="s1", title="t", events=[])
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.session.get_by_id = AsyncMock(return_value=session)
    uow.session.list_events = AsyncMock(return_value=[])

    llm = MagicMock()
    llm.invoke = AsyncMock(side_effect=RuntimeError("boom"))
    json_parser = MagicMock()

    service = MemoryExtractorService(lambda: uow, llm, json_parser)
    entries = await service.extract_from_session("s1")
    assert entries == []


def test_memory_extractor_returns_empty_on_failure():
    asyncio.run(_run_memory_extractor_returns_empty_on_failure())


async def _run_memory_extractor_skips_when_session_deleted_before_save():
    session = Session(id="s1", title="t", events=[])
    uow_read = MagicMock()
    uow_read.__aenter__ = AsyncMock(return_value=uow_read)
    uow_read.__aexit__ = AsyncMock(return_value=None)
    uow_read.session.get_by_id = AsyncMock(return_value=session)
    uow_read.session.list_events = AsyncMock(return_value=[])

    uow_write = MagicMock()
    uow_write.__aenter__ = AsyncMock(return_value=uow_write)
    uow_write.__aexit__ = AsyncMock(return_value=None)
    uow_write.session.get_by_id = AsyncMock(return_value=None)
    uow_write.memory_entry.save = AsyncMock()

    uow_calls = [uow_read, uow_write]

    llm = MagicMock()
    llm.invoke = AsyncMock(return_value={
        "content": '[{"title":"t","content":"c","tags":["a"]}]',
    })
    json_parser = MagicMock()
    json_parser.invoke = AsyncMock(return_value=[{"title": "t", "content": "c", "tags": ["a"]}])

    service = MemoryExtractorService(lambda: uow_calls.pop(0), llm, json_parser)
    entries = await service.extract_from_session("s1")
    assert entries == []
    uow_write.memory_entry.save.assert_not_called()


def test_memory_extractor_skips_when_session_deleted_before_save():
    asyncio.run(_run_memory_extractor_skips_when_session_deleted_before_save())
