"""Original text pagination restricted to a prepared context's source closure."""

import hashlib
import hmac
import re
from dataclasses import dataclass

from app.domain.execution.context_models import AcceptedContextCut, context_digest


@dataclass(frozen=True)
class OriginalSourcePage:
    source_id: str
    digest: str
    text: str
    offset: int
    total_bytes: int
    next_cursor: str | None


class ContextReadSource:
    def __init__(self, *, reader, validator, signing_secret, descriptors=None):
        if not signing_secret:
            raise ValueError("context source cursor authority missing")
        self.reader = reader
        self.descriptors = descriptors
        self.validator = validator
        self.secret = signing_secret.encode()

    def _cursor(self, manifest, source, offset):
        material = context_digest(
            {
                "manifest": manifest.digest,
                "source": source.model_dump(mode="json"),
                "offset": offset,
            }
        )
        signature = hmac.new(
            self.secret, ("context-original-page:v1:" + material).encode(), hashlib.sha256
        ).hexdigest()
        return f"{offset}.{signature}"

    async def read(self, manifest, *, source_id, cursor=None, limit=8192):
        if type(limit) is not int or not 4 <= limit <= 65536:
            raise ValueError("CONTEXT_READ_LIMIT_INVALID")
        source = next(
            (item for item in manifest.original_closure if item.source_id == source_id), None
        )
        if source is None or source.scope_key != manifest.boundary.scope_key:
            raise ValueError("CONTEXT_SOURCE_UNAVAILABLE")
        offset = 0
        if cursor is not None:
            if (
                not isinstance(cursor, str)
                or re.fullmatch(r"[0-9]{1,8}\.[0-9a-f]{64}", cursor) is None
            ):
                raise ValueError("CONTEXT_SOURCE_CURSOR_INVALID")
            offset = int(cursor.split(".", 1)[0])
            if not hmac.compare_digest(cursor, self._cursor(manifest, source, offset)):
                raise ValueError("CONTEXT_SOURCE_CURSOR_INVALID")
        # Validate the entire transitive closure, including dependencies of a
        # selected assistant answer, before returning any original text.
        await self.validator.validate(manifest.boundary, manifest.original_closure)
        if self.descriptors is None:
            text, following, total = await self.reader.read_source_span(
                manifest, source, offset=offset, limit=limit
            )
            await self.validator.validate(manifest.boundary, manifest.original_closure)
            return OriginalSourcePage(
                source_id=source.source_id,
                digest=source.digest,
                text=text,
                offset=offset,
                total_bytes=total,
                next_cursor=self._cursor(manifest, source, following)
                if following is not None
                else None,
            )
        descriptor = await self.descriptors.resolve(manifest.boundary, source)
        if (
            descriptor.source_id != source.source_id
            or descriptor.run_id != source.run_id
            or descriptor.kind != source.kind
            or descriptor.formal_position != source.formal_position
            or descriptor.object_digest != source.digest
        ):
            raise ValueError("CONTEXT_SOURCE_IDENTITY_CONFLICT")
        cut = AcceptedContextCut(
            scope_key=manifest.boundary.scope_key,
            collection_id=manifest.boundary.collection_id,
            through_position=manifest.boundary.through_position,
        )
        text, following, total = await self.reader.read_span(
            cut, descriptor, offset=offset, limit=limit
        )
        await self.validator.validate(manifest.boundary, manifest.original_closure)
        return OriginalSourcePage(
            source_id=source.source_id,
            digest=source.digest,
            text=text,
            offset=offset,
            total_bytes=total,
            next_cursor=self._cursor(manifest, source, following)
            if following is not None
            else None,
        )
