"""Producer-bound indexes; every bounded read verifies its complete stored chunk."""

import hashlib
import json

from app.application.execution.context_io import read_bounded
from app.application.execution.object_writes import put_execution_object
from app.domain.execution.context_index import OriginalContentChunk, OriginalContentIndex
from app.domain.execution.serialization import canonical_json_bytes
from app.domain.external.object_storage import ObjectNotFoundError


class ContextObjectStore:
    def __init__(self, storage):
        self.storage = storage

    async def _read(self, ref, limit):
        try:
            return await read_bounded(self.storage, ref, limit)
        except ObjectNotFoundError as error:
            raise ValueError("CONTEXT_SOURCE_UNAVAILABLE") from error

    async def put_result(self, *, run_id, activity_id, scope_key, payload):
        message = payload.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ValueError("CONTEXT_INDEX_MESSAGE_INVALID")  # noqa: TRY004 - stable failure code
        original = canonical_json_bytes(payload)
        if len(original) > 8388608:
            raise ValueError("CONTEXT_INDEX_RESULT_TOO_LARGE")
        original_digest = hashlib.sha256(original).hexdigest()
        original_ref = f"execution/results/{activity_id}/{original_digest}.json"
        await put_execution_object(
            self.storage, original_ref, original, run_id=run_id, scope_key=scope_key
        )
        content = message["content"].encode()
        chunks = []
        offset = 0
        while offset < len(content):
            end = min(len(content), offset + 65536)
            # Keep each chunk independently decodable. A UTF-8 character has
            # at most four bytes, so this retreats by at most three bytes.
            while end < len(content) and content[end] & 0xC0 == 0x80:
                end -= 1
            body = content[offset:end]
            digest = hashlib.sha256(body).hexdigest()
            key = f"execution/context_sources/{run_id}/{activity_id}/{digest}.txt"
            await put_execution_object(self.storage, key, body, run_id=run_id, scope_key=scope_key)
            chunks.append(
                OriginalContentChunk(
                    offset=offset, byte_length=len(body), digest=digest, object_ref=key
                )
            )
            offset = end
        index = OriginalContentIndex(
            scope_key=scope_key,
            run_id=run_id,
            activity_id=activity_id,
            original_ref=original_ref,
            original_digest=original_digest,
            original_bytes=len(original),
            content_digest=hashlib.sha256(content).hexdigest(),
            content_bytes=len(content),
            message_metadata={key: value for key, value in message.items() if key != "content"},
            resource_evidence=payload.get("resource_evidence"),
            chunks=tuple(chunks),
        )
        encoded = canonical_json_bytes({"_context_index": index.model_dump(mode="json")})
        if len(encoded) > 262144:
            raise ValueError("CONTEXT_INDEX_METADATA_TOO_LARGE")
        digest = hashlib.sha256(encoded).hexdigest()
        ref = f"execution/results/{activity_id}/{digest}.json"
        await put_execution_object(self.storage, ref, encoded, run_id=run_id, scope_key=scope_key)
        return ref, index

    async def load_index(self, *, ref, digest, scope_key, run_id, read_limit=65536):
        if (
            not 1 <= read_limit <= 262144
            or not ref.startswith("execution/results/")
            or not ref.endswith(f"/{digest}.json")
        ):
            raise ValueError("CONTEXT_INDEX_REFERENCE_INVALID")
        bounded = await self._read(ref, read_limit)
        if bounded.truncated:
            raise ValueError("CONTEXT_INDEX_METADATA_TOO_LARGE")
        if hashlib.sha256(bounded.data).hexdigest() != digest:
            raise ValueError("CONTEXT_SOURCE_INTEGRITY_INVALID")
        index = OriginalContentIndex.model_validate(json.loads(bounded.data)["_context_index"])
        if ref != f"execution/results/{index.activity_id}/{digest}.json":
            raise ValueError("CONTEXT_INDEX_REFERENCE_INVALID")
        if index.scope_key != scope_key or index.run_id != run_id:
            raise ValueError("CONTEXT_SOURCE_SCOPE_MISMATCH")
        return index

    async def read_span(self, index, *, offset=0, limit=65536, before_read):
        if (
            type(offset) is not int
            or type(limit) is not int
            or offset < 0
            or offset > index.content_bytes
            or not 1 <= limit <= 262144
        ):
            raise ValueError("CONTEXT_READ_LIMIT_INVALID")
        end = min(index.content_bytes, offset + limit)
        parts = []
        for chunk in index.chunks:
            chunk_end = chunk.offset + chunk.byte_length
            if chunk_end <= offset or chunk.offset >= end:
                continue
            await before_read(index)
            bounded = await self._read(chunk.object_ref, 65536)
            if (
                bounded.truncated
                or len(bounded.data) != chunk.byte_length
                or hashlib.sha256(bounded.data).hexdigest() != chunk.digest
            ):
                raise ValueError("CONTEXT_SOURCE_INTEGRITY_INVALID")
            part = bounded.data[
                max(0, offset - chunk.offset) : min(chunk.byte_length, end - chunk.offset)
            ]
            parts.append(part)
        data = b"".join(parts)
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as error:
            if (
                error.reason == "unexpected end of data"
                and error.end == len(data)
                and error.start > 0
            ):
                data = data[: error.start]
                end = offset + len(data)
            else:
                raise ValueError("CONTEXT_READ_UTF8_BOUNDARY_INVALID") from error
        return data, end if end < index.content_bytes else None
