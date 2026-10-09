"""One durable export per supervisor iteration, immutable bounded verified chunks."""

import asyncio
import hashlib
from datetime import UTC, datetime
from time import monotonic

from app.application.services.execution_export_encoding import (
    ExportColumn,
    ExportEncoder,
    safe_json,
)

CHUNK_BYTES = 1024 * 1024
IO_TIMEOUT = 30
MAX_PENDING_WRITES = 5


class ExecutionExportWorker:
    def __init__(self, repository, objects):
        self.repository, self.objects = repository, objects
        self._writes = {}

    @property
    def pending_writes(self):
        return len(self._writes)

    async def reap_writes(self):
        for task, (lease, chunk) in list(self._writes.items()):
            if not task.done():
                continue
            if task.cancelled() or task.exception() is not None:
                # Unknown SDK outcomes remain durably cleanup-inventoried.
                del self._writes[task]
                continue
            # Keep the positive completion retriable if acknowledgement fails.
            await self.repository.write_completed(lease, chunk)
            del self._writes[task]

    async def close(self):
        # Cancelling the awaiter cannot stop an SDK thread. Do not acknowledge
        # these unknown writes: durable inventory survives process shutdown.
        tasks = tuple(self._writes)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks, timeout=0.1)
        for task in tasks:
            if task.done() and not task.cancelled():
                task.exception()

    async def process_pending(self):
        await self.reap_writes()
        if self.pending_writes >= MAX_PENDING_WRITES:
            return 0
        lease = await self.repository.claim()
        if lease is None:
            return 0
        try:
            async with asyncio.timeout(600):
                await self._generate(lease)
        except Exception as error:  # noqa: BLE001 - fail the fenced attempt; cancellation remains reclaimable
            reason = str(error)
            code = (
                reason
                if reason
                in {"export_capacity_exceeded", "export_capture_mismatch", "export_corrupt_object"}
                else "export_generation_failed"
            )
            await self.repository.fail(lease, code=code)
        return 1

    async def _generate(self, lease):
        repo = self.repository
        await repo.current(lease.scope, lease.principal, lease.export_id)
        header = await repo.header(lease)
        encoder = ExportEncoder(
            header["format"],
            header["metadata"],
            header["metrics"],
            [ExportColumn(**c) for c in header["columns"]],
        )
        buffer = bytearray()
        chunks = []
        digest = hashlib.sha256()
        total = 0
        renewed_at = monotonic()

        async def renew():
            nonlocal renewed_at
            if datetime.now(UTC) >= lease.expires_at:
                raise ValueError("export_expired")
            if monotonic() - renewed_at >= 30:
                await repo.renew(lease)
                renewed_at = monotonic()

        async def upload(data):
            nonlocal total
            await renew()
            if len(chunks) >= 128:
                raise ValueError("export_capacity_exceeded")
            chunk_digest = hashlib.sha256(data).hexdigest()
            chunk = await repo.begin_chunk(
                lease, ordinal=len(chunks), size=len(data), digest=chunk_digest
            )
            # A concrete repository supplies the shared object-ownership lock. It
            # must recheck the durable intent/lease while holding that lock.
            async with repo.chunk_io(lease, chunk):
                task = asyncio.create_task(self.objects.put_bytes(chunk.key, data))
                self._writes[task] = (lease, chunk)
                async with asyncio.timeout(IO_TIMEOUT):
                    await asyncio.shield(task)
                async with asyncio.timeout(30):
                    observed = await self.objects.get_bounded_bytes(chunk.key, len(data))
                if (
                    observed.truncated
                    or len(observed.data) != len(data)
                    or hashlib.sha256(observed.data).hexdigest() != chunk_digest
                ):
                    raise ValueError("export_corrupt_object")
            await self.reap_writes()
            digest.update(data)
            total += len(data)
            chunks.append(chunk)

        async def consume(data):
            for offset in range(0, len(data), CHUNK_BYTES):
                buffer.extend(data[offset : offset + CHUNK_BYTES])
                if len(buffer) >= CHUNK_BYTES:
                    await upload(bytes(buffer[:CHUNK_BYTES]))
                    del buffer[:CHUNK_BYTES]

        for part in encoder.header():
            await consume(part)
        after = -1
        while True:
            await renew()
            async with asyncio.timeout(30):
                page = await repo.page(lease, after=after, limit=200)
            if len(page["rows"]) > 200 or len(safe_json(page["rows"]).encode()) > CHUNK_BYTES:
                raise ValueError("export_capacity_exceeded")
            for row in page["rows"]:
                await consume(encoder.row(row))
            next_after = page["next_after"]
            if next_after is None:
                break
            if type(next_after) is not int or next_after <= after or not page["rows"]:
                raise ValueError("export_capture_mismatch")
            after = next_after
        for part in encoder.finish():
            await consume(part)
        if buffer:
            await upload(bytes(buffer))
        await renew()
        # No object I/O follows this fresh full-proof barrier. Publication repeats
        # the proof atomically with fencing, complete manifest and expiry checks.
        await repo.current(lease.scope, lease.principal, lease.export_id)
        await repo.publish(lease, tuple(chunks), size=total, digest=digest.hexdigest())
