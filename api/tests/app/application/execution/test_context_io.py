import asyncio

import pytest

from app.application.execution.context_io import ContextReadBudget, read_bounded
from app.domain.external.object_storage import BoundedObjectBytes
from tests.app.application.execution.test_context_objects import Storage

pytestmark = pytest.mark.asyncio


async def test_total_actual_bytes_are_limited_before_another_object_read():
    storage = Storage()
    for key in ("source", "source2", "source3"):
        storage.objects[key] = b"x" * 37
    with ContextReadBudget(100) as budget:
        assert (await read_bounded(storage, "source", 64)).data == b"x" * 37
        await read_bounded(storage, "source2", 64)
        assert budget.consumed == 74
        with pytest.raises(ValueError, match="CONTEXT_PREPARE_READ_BUDGET_EXCEEDED"):
            await read_bounded(storage, "source3", 64)
        assert storage.reads[-1] == ("source3", 25)
        assert budget.consumed == 100
        calls = len(storage.reads)
        with pytest.raises(ValueError, match="CONTEXT_PREPARE_READ_BUDGET_EXCEEDED"):
            await read_bounded(storage, "source3", 64)
        assert len(storage.reads) == calls
    assert (await read_bounded(storage, "source", 64)).data == b"x" * 37


async def test_parallel_child_reads_reserve_from_the_same_budget_before_awaiting():
    class ConcurrentStorage:
        def __init__(self):
            self.limits = []

        async def get_bounded_bytes(self, key, limit):
            self.limits.append(limit)
            await asyncio.sleep(0)
            return BoundedObjectBytes(data=b"x" * min(8, limit), truncated=limit < 8)

    storage = ConcurrentStorage()
    with ContextReadBudget(12) as budget:
        results = await asyncio.gather(
            read_bounded(storage, "a", 8),
            read_bounded(storage, "b", 8),
            return_exceptions=True,
        )
        assert storage.limits == [8, 2]
        assert results[0].data == b"x" * 8
        assert str(results[1]) == "CONTEXT_PREPARE_READ_BUDGET_EXCEEDED"
        assert budget.consumed == 11


async def test_separate_preparations_have_independent_budgets_and_reset_on_error():
    storage = Storage()
    storage.objects["source"] = b"x" * 8

    async def prepare():
        with ContextReadBudget(10) as budget:
            await asyncio.sleep(0)
            await read_bounded(storage, "source", 16)
            return budget.consumed

    assert await asyncio.gather(prepare(), prepare()) == [8, 8]
    with (
        pytest.raises(ValueError, match="CONTEXT_READ_BUDGET_INVALID"),
        ContextReadBudget(20),
        ContextReadBudget(20),
    ):
        pass
    assert await prepare() == 8


async def test_immutable_object_memo_is_invocation_local_and_does_not_relax_span_limits():
    storage = Storage()
    storage.objects["source"] = b"exact fact"
    with ContextReadBudget(100) as budget:
        assert (await read_bounded(storage, "source", 16)).data == b"exact fact"
        smaller = await read_bounded(storage, "source", 4)
        assert smaller.data == b"exac"
        assert smaller.truncated
        assert (await read_bounded(storage, "source", 32)).data == b"exact fact"
        assert budget.consumed == 10
        assert len(storage.reads) == 1
    storage.objects["source"] = b"changed"
    with ContextReadBudget(100):
        assert (await read_bounded(storage, "source", 16)).data == b"changed"
    assert len(storage.reads) == 2
