#!/usr/bin/env python
# -*- coding: utf-8 -*-
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.models.error_codes import DOCUMENT_PARSE_FAILED
from app.domain.models.event import DoneEvent, ErrorEvent
from app.domain.services.knowledge_base.ingest_errors import NonRecoverableIngestError
from app.domain.services.knowledge_base.ingestion_task_runner import KBIngestionTaskRunner


class _ErrorRunner:
    async def run(self, _kb_id):
        yield ErrorEvent(error="全部文档解析失败", code=DOCUMENT_PARSE_FAILED)


class _SuccessRunner:
    async def run(self, _kb_id):
        yield DoneEvent()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_invoke_raises_when_error_without_done():
    runner = KBIngestionTaskRunner(
        uow_factory=MagicMock(),
        file_storage=MagicMock(),
        kb_id="kb1",
    )
    runner._runner = _ErrorRunner()
    task = MagicMock()
    task.output_stream.put = AsyncMock()

    with pytest.raises(NonRecoverableIngestError, match="全部文档解析失败"):
        await runner.invoke(task)

    task.output_stream.put.assert_awaited_once()


@pytest.mark.anyio
async def test_invoke_completes_on_done_event():
    runner = KBIngestionTaskRunner(
        uow_factory=MagicMock(),
        file_storage=MagicMock(),
        kb_id="kb1",
    )
    runner._runner = _SuccessRunner()
    task = MagicMock()
    task.output_stream.put = AsyncMock()

    await runner.invoke(task)

    task.output_stream.put.assert_awaited_once()
