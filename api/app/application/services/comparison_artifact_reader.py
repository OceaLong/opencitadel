"""Current authorized bytes from a retained artifact/version/producer-cut binding."""

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class FixedArtifactBytes:
    artifact_id: str
    version: int
    kind: str
    data: bytes
    truncated: bool
    verified_digest: str | None

    @property
    def public_metadata(self):
        return {
            "artifact_id": self.artifact_id,
            "version": self.version,
            "kind": self.kind,
            "availability": "available",
            "verified_digest": self.verified_digest,
            "truncated": self.truncated,
        }


class ComparisonArtifactReader:
    def __init__(self, comparisons, objects):
        self.comparisons, self.objects = comparisons, objects

    async def read(self, scope, principal, comparison_id, revision, selection, *, limit):
        if type(limit) is not int or not 1 <= limit <= 2097152:
            raise ValueError("invalid_artifact_read_limit")
        metadata = await self.comparisons.artifact_context(
            scope, principal, comparison_id, revision, selection
        )
        value = await self.objects.get_bounded_bytes(metadata["storage_key"], limit)
        current = await self.comparisons.artifact_context(
            scope, principal, comparison_id, revision, selection
        )
        if current != metadata:
            raise PermissionError("artifact_authority_changed")
        digest = None
        if not value.truncated:
            digest = "sha256:" + hashlib.sha256(value.data).hexdigest()
            if digest != metadata["content_digest"]:
                raise ValueError("immutable_artifact_changed")
        return FixedArtifactBytes(
            metadata["artifact_id"],
            metadata["version"],
            metadata["kind"],
            value.data,
            value.truncated,
            digest,
        )
