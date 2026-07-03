#!/usr/bin/env python
# -*- coding: utf-8 -*-
import asyncio
from unittest.mock import AsyncMock, patch

from app.infrastructure.external import event_seq_allocator


class _SharedRedisCounter:
    """Simulates a single Redis counter shared across API/Worker processes."""

    def __init__(self, start: int = 0) -> None:
        self._value = start

    async def incrby(self, key: str, amount: int) -> int:
        assert key == event_seq_allocator.GLOBAL_EVENT_SEQ_KEY
        assert amount == event_seq_allocator._BLOCK_SIZE
        self._value += amount
        return self._value


def _patch_redis(counter: _SharedRedisCounter):
    redis = AsyncMock()
    redis.client.incrby = AsyncMock(side_effect=counter.incrby)
    return patch("app.infrastructure.external.event_seq_allocator.get_redis", return_value=redis)


async def _test_allocate_uses_atomic_incrby_each_call():
    counter = _SharedRedisCounter(start=0)
    with _patch_redis(counter):
        seq_a = await event_seq_allocator.allocate_event_seq()
        seq_b = await event_seq_allocator.allocate_event_seq()
        seq_c = await event_seq_allocator.allocate_event_seq()

    assert (seq_a, seq_b, seq_c) == (1, 2, 3)


def test_allocate_uses_atomic_incrby_each_call():
    asyncio.run(_test_allocate_uses_atomic_incrby_each_call())


async def _test_cross_process_pattern_preserves_causal_order():
    """API user msg -> Worker plan gate events -> API approve must stay monotonic."""
    counter = _SharedRedisCounter(start=0)
    with _patch_redis(counter):
        user_question = await event_seq_allocator.allocate_event_seq()
        assistant_notice = await event_seq_allocator.allocate_event_seq()
        plan_event = await event_seq_allocator.allocate_event_seq()
        approval_event = await event_seq_allocator.allocate_event_seq()
        wait_event = await event_seq_allocator.allocate_event_seq()
        approve_message = await event_seq_allocator.allocate_event_seq()

    assert user_question < assistant_notice < plan_event < approval_event < wait_event
    assert approve_message > wait_event
    assert approve_message == wait_event + 1


def test_cross_process_pattern_preserves_causal_order():
    asyncio.run(_test_cross_process_pattern_preserves_causal_order())


async def _test_alternating_allocators_share_one_global_sequence():
    counter = _SharedRedisCounter(start=100)
    sequences: list[int] = []

    async def api_allocate() -> int:
        with _patch_redis(counter):
            return await event_seq_allocator.allocate_event_seq()

    async def worker_allocate() -> int:
        with _patch_redis(counter):
            return await event_seq_allocator.allocate_event_seq()

    sequences.append(await api_allocate())
    sequences.append(await worker_allocate())
    sequences.append(await worker_allocate())
    sequences.append(await api_allocate())
    sequences.append(await worker_allocate())

    assert sequences == [101, 102, 103, 104, 105]


def test_alternating_allocators_share_one_global_sequence():
    asyncio.run(_test_alternating_allocators_share_one_global_sequence())


async def _test_block_size_is_one():
    assert event_seq_allocator._BLOCK_SIZE == 1


def test_block_size_is_one():
    asyncio.run(_test_block_size_is_one())


async def _test_regression_old_block_prefetch_inverts_replay_order():
    """Old _BLOCK_SIZE=64 assigned approve seq=2 and assistant_notice seq=65."""
    api_block_start = 1
    worker_block_start = 65
    user_question_seq = api_block_start
    assistant_notice_seq = worker_block_start
    approve_seq = api_block_start + 1

    assert approve_seq < assistant_notice_seq

    counter = _SharedRedisCounter(start=0)
    with _patch_redis(counter):
        replay = [
            await event_seq_allocator.allocate_event_seq(),  # user question
            await event_seq_allocator.allocate_event_seq(),  # assistant_notice
            await event_seq_allocator.allocate_event_seq(),  # plan
            await event_seq_allocator.allocate_event_seq(),  # approval
            await event_seq_allocator.allocate_event_seq(),  # wait
            await event_seq_allocator.allocate_event_seq(),  # approve
        ]

    assert replay[0] < replay[1] < replay[5]
    assert replay[5] > replay[1]


def test_regression_old_block_prefetch_inverts_replay_order():
    asyncio.run(_test_regression_old_block_prefetch_inverts_replay_order())

