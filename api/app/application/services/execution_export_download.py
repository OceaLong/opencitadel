"""Verify private immutable chunks into an anonymous spool before releasing bytes."""

import asyncio
import hashlib
import tempfile


class ExportDownloader:
    def __init__(self, repository, objects):
        self.repository, self.objects = repository, objects

    async def prepare(self, scope, principal, export_id):
        repo = self.repository
        manifest = await repo._call(scope, principal, "acquire_use", export_id=export_id)
        spool = tempfile.TemporaryFile(mode="w+b")  # noqa: SIM115 - ownership transfers to response stream
        try:
            async with asyncio.timeout(540):
                chunks = manifest["chunks"]
                if not 1 <= len(chunks) <= 128 or not 1 <= manifest["size"] <= 128 * 1024 * 1024:
                    raise ValueError("export_corrupt_object")
                digest = hashlib.sha256()
                total = 0
                for ordinal, chunk in enumerate(chunks):
                    if chunk["ordinal"] != ordinal or not 1 <= chunk["size"] <= 1024 * 1024:
                        raise ValueError("export_corrupt_object")
                    async with asyncio.timeout(30):
                        observed = await self.objects.get_bounded_bytes(chunk["key"], chunk["size"])
                    if (
                        observed.truncated
                        or len(observed.data) != chunk["size"]
                        or hashlib.sha256(observed.data).hexdigest() != chunk["digest"]
                    ):
                        raise ValueError("export_corrupt_object")
                    total += len(observed.data)
                    if total > manifest["size"]:
                        raise ValueError("export_corrupt_object")
                    digest.update(observed.data)
                    spool.write(observed.data)
                if total != manifest["size"] or digest.hexdigest() != manifest["digest"]:
                    raise ValueError("export_corrupt_object")
                # Fresh primary READ COMMITTED proof follows every provider read.
                await repo._call(
                    scope, principal, "finish_use", export_id=export_id, use_id=manifest["use_id"]
                )
                spool.seek(0)
                return spool, manifest["format"], total
        except BaseException:
            spool.close()
            raise
        finally:
            # The local anonymous spool no longer depends on object retention.
            try:
                await asyncio.shield(
                    repo._call(
                        scope,
                        principal,
                        "release_use",
                        export_id=export_id,
                        use_id=manifest["use_id"],
                    )
                )
            except BaseException:
                spool.close()
                raise

    @staticmethod
    async def stream(spool):
        try:
            while data := spool.read(65536):
                yield data
        finally:
            spool.close()
