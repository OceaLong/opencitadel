"""Current fixed artifact authority is checked before and after bounded object I/O."""

import hashlib
from importlib.util import find_spec

import pytest

from app.domain.external.object_storage import BoundedObjectBytes

pytestmark = pytest.mark.asyncio


class Metadata:
    def __init__(self, data):
        self.data, self.revoked, self.digest = (
            data,
            False,
            "sha256:" + hashlib.sha256(data).hexdigest(),
        )

    async def artifact_context(self, *args):
        if self.revoked:
            raise PermissionError("revoked")
        return {
            "artifact_id": "artifact",
            "version": 1,
            "content_digest": self.digest,
            "kind": "doc",
            "storage_key": "private-key",
        }


class Storage:
    def __init__(self, metadata, revoke=False):
        self.metadata, self.revoke = metadata, revoke

    async def get_bounded_bytes(self, key, limit):
        assert key == "private-key"
        self.metadata.revoked = self.revoke
        data = self.metadata.data
        return BoundedObjectBytes(data[:limit], len(data) > limit)


def reader(metadata, storage):
    assert find_spec("app.application.services.comparison_artifact_reader") is not None, (
        "fixed comparison artifact reader missing"
    )
    from app.application.services.comparison_artifact_reader import ComparisonArtifactReader

    return ComparisonArtifactReader(metadata, storage)


async def test_revocation_during_object_read_suppresses_body():
    metadata = Metadata(b"secret")
    with pytest.raises(PermissionError):
        await reader(metadata, Storage(metadata, True)).read(None, None, "id", 1, {}, limit=65536)


async def test_fixed_digest_mismatch_is_not_a_valid_diff_input():
    metadata = Metadata(b"original")
    metadata.data = b"changed"
    with pytest.raises(ValueError, match="immutable_artifact_changed"):
        await reader(metadata, Storage(metadata)).read(None, None, "id", 1, {}, limit=65536)


async def test_prefix_truncation_does_not_verify_full_digest_or_leak_storage_key():
    metadata = Metadata(b"a" * 70000)
    value = await reader(metadata, Storage(metadata)).read(None, None, "id", 1, {}, limit=65536)
    assert value.truncated is True
    assert value.verified_digest is None
    assert value.public_metadata == {
        "artifact_id": "artifact",
        "version": 1,
        "kind": "doc",
        "availability": "available",
        "verified_digest": None,
        "truncated": True,
    }
