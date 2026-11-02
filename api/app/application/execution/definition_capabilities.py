"""Renew exact local capability leases independently of business task claims."""

import asyncio
from datetime import timedelta


class DefinitionCapabilityPublisher:
    def __init__(
        self,
        *,
        store_context,
        definitions,
        capabilities,
        ttl=timedelta(seconds=30),
        interval_seconds=10.0,
    ):
        if interval_seconds <= 0 or ttl.total_seconds() <= interval_seconds * 2:
            raise ValueError("capability heartbeat must precede its expiry")
        for manifest in definitions.manifests:
            if not capabilities.supports(manifest):
                raise ValueError("local capability closure is incomplete")
        self.store_context = store_context
        self.definitions = definitions
        self.capabilities = capabilities
        self.ttl = ttl
        self.interval_seconds = interval_seconds

    async def run_once(self):
        async with self.store_context() as store:
            for manifest in self.definitions.manifests:
                await store.register(manifest)
            await store.heartbeat(self.capabilities, ttl=self.ttl)

    async def serve(self, stop: asyncio.Event):
        while not stop.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                continue
