import hashlib
from types import SimpleNamespace

import pytest

from app.application.services.execution_export_download import ExportDownloader


@pytest.mark.asyncio
async def test_download_rechecks_authority_after_objects_and_closes_private_spool():
    events = []
    data = b"safe"
    digest = hashlib.sha256(data).hexdigest()
    manifest = {
        "use_id": "lease",
        "format": "csv",
        "size": 4,
        "digest": digest,
        "chunks": [{"key": "private", "size": 4, "digest": digest, "ordinal": 0}],
    }

    class Repo:
        async def _call(self, scope, principal, operation, **payload):
            events.append(operation)
            if operation == "acquire_use":
                return manifest
            if operation == "finish_use":
                raise PermissionError("revoked")
            return None

    class Objects:
        async def get_bounded_bytes(self, key, size):
            events.append("read")
            return SimpleNamespace(data=data, truncated=False)

    with pytest.raises(PermissionError, match="revoked"):
        await ExportDownloader(Repo(), Objects()).prepare("scope", "principal", "job")
    assert events == ["acquire_use", "read", "finish_use", "release_use"]


@pytest.mark.asyncio
async def test_corrupt_chunks_are_never_returned():
    digest = hashlib.sha256(b"safe").hexdigest()

    class Repo:
        async def _call(self, scope, principal, operation, **payload):
            if operation == "acquire_use":
                return {
                    "use_id": "lease",
                    "format": "json",
                    "size": 4,
                    "digest": digest,
                    "chunks": [{"key": "private", "size": 4, "digest": digest, "ordinal": 0}],
                }
            assert operation == "release_use"
            return None

    class Objects:
        async def get_bounded_bytes(self, key, size):
            return SimpleNamespace(data=b"evil", truncated=False)

    with pytest.raises(ValueError, match="export_corrupt_object"):
        await ExportDownloader(Repo(), Objects()).prepare(None, None, "job")
