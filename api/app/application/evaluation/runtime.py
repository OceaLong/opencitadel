"""Kernel-only lifecycle orchestration over durable, independently fenced consumers.

Inventory is discovery, never authority. Each consumer revalidates the original
principal and current execution/evaluation revision at its mutation boundary.
"""

import asyncio
from contextlib import suppress
from datetime import UTC, datetime


class EvaluationRuntime:
    def __init__(self, *, scheduler, rules, judge, reviews, discover, cleanup):
        self.scheduler = scheduler
        self.rules, self.judge, self.reviews = rules, judge, reviews
        self.discover, self.cleanup = discover, tuple(cleanup)

    async def schedule(self):
        return await self.scheduler.tick(datetime.now(UTC))

    async def score(self):
        for scope, batch_id in await self.discover():
            try:
                await self.rules.score_batch(scope, batch_id, limit=100)
                await self.judge.score_batch(scope, batch_id, limit=100)
            except PermissionError:
                # Revocation is local to this owner; scheduler settles its batch.
                continue

    async def reconcile(self):
        # Includes settled/stopped intents: late physical facts arrive without
        # a Run revision change. Review receipts also need independent refresh.
        await self.judge.tick(limit=100)
        await self.reviews.tick(limit=100)

    async def clean(self):
        for action in self.cleanup:
            await action()

    @staticmethod
    async def run(action, *, stop_event, interval_seconds=1.0):
        if interval_seconds <= 0:
            raise ValueError("invalid_evaluation_interval")
        while not stop_event.is_set():
            # An unavailable durable store must withdraw kernel readiness, not
            # leave a silently dead evaluation service marked ready.
            await action()
            with suppress(TimeoutError):
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
