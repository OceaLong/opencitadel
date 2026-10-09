from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import DBAPIError

from app.infrastructure.repositories.db_execution_comparison_repository import (
    DBExecutionComparisonRepository,
)


@pytest.mark.asyncio
async def test_receipt_snapshot_conflict_retries_a_new_signed_transaction():
    opened = []

    @asynccontextmanager
    async def transaction(*args):
        token = object()
        opened.append(token)
        yield token

    repo = DBExecutionComparisonRepository(None, signing_secret="test-secret")
    repo.transactions = SimpleNamespace(transaction=transaction)

    class Retry(Exception):
        sqlstate = "40001"

    async def operation(db, *args, **kwargs):
        if len(opened) == 1:
            raise DBAPIError("receipt", {}, Retry("snapshot"), False)
        return {"alignment_revision": 1}

    repo._operation = operation
    result = await repo.align(
        None, None, "comparison", 1, expected_revision=0, edits=[], request_id="same-request"
    )
    assert result == 1
    assert len(opened) == 2
    assert opened[0] is not opened[1]
