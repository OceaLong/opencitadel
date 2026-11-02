"""Content-addressed input/result objects for durable Activities."""

import hashlib
import json
import zlib
from uuid import UUID

from app.application.execution.context_io import read_bounded
from app.application.execution.object_writes import object_scope, put_execution_object
from app.domain.execution.commands import JsonValue
from app.domain.external.object_storage import ObjectNotFoundError, ObjectStoragePort


class ActivityObjectStore:
    def __init__(self, object_storage: ObjectStoragePort) -> None:
        self._storage = object_storage

    async def put_input(
        self,
        run_id: UUID,
        payload: dict[str, JsonValue],
        *,
        scope=None,
    ) -> tuple[str, str]:
        encoded = self._encode(payload)
        digest = hashlib.sha256(encoded).hexdigest()
        key = f"execution/inputs/{run_id}/{digest}.json"
        await put_execution_object(
            self._storage,
            key,
            encoded,
            run_id=run_id,
            scope_key=object_scope(scope) if scope else None,
        )
        return key, digest

    async def guard_publication(self, run_id, scope, work):
        guard = getattr(self._storage, "guard_publication", None)
        if guard is not None:
            if work is None:
                raise ValueError("CONTEXT_ADMISSION_TRANSACTION_REQUIRED")
            await guard(work.db_session, object_scope(scope), run_id)

    async def load_input(
        self,
        *,
        key: str,
        expected_digest: str,
    ) -> dict[str, JsonValue]:
        encoded = await self._storage.get_bytes(key)
        actual = hashlib.sha256(encoded).hexdigest()
        if actual != expected_digest:
            raise ValueError("Activity input digest mismatch")
        payload = json.loads(encoded)
        if not isinstance(payload, dict):
            raise TypeError("Activity input must be a JSON object")
        return payload

    async def load_input_bounded(self, *, key, expected_digest, limit=65536):
        if type(limit) is not int or not 1 <= limit <= 262144:
            raise ValueError("CONTEXT_READ_LIMIT_INVALID")
        try:
            bounded = await read_bounded(self._storage, key, limit)
        except ObjectNotFoundError as error:
            raise ValueError("CONTEXT_SOURCE_UNAVAILABLE") from error
        if bounded.truncated:
            raise ValueError("CONTEXT_SOURCE_REQUIRES_INDEXED_EXCERPT")
        if hashlib.sha256(bounded.data).hexdigest() != expected_digest:
            raise ValueError("CONTEXT_SOURCE_INTEGRITY_INVALID")
        payload = json.loads(bounded.data)
        if not isinstance(payload, dict):
            raise ValueError("CONTEXT_SOURCE_CONTENT_INVALID")  # noqa: TRY004 - stable failure code
        return payload

    async def put_result(
        self,
        activity_id: UUID,
        payload: dict[str, JsonValue],
        *,
        run_id=None,
        scope_key=None,
    ) -> str:
        encoded = self._encode(payload)
        digest = hashlib.sha256(encoded).hexdigest()
        key = f"execution/results/{activity_id}/{digest}.json"
        await put_execution_object(self._storage, key, encoded, run_id=run_id, scope_key=scope_key)
        return key

    async def put_context_result(self, activity_id, payload, *, run):
        from app.application.execution.context_objects import ContextObjectStore

        policy = run.definition_manifest.context_policy
        if len(self._encode(payload)) <= policy.read_bytes // 2:
            return await self.put_result(activity_id, payload, run_id=run.run_id)
        scope = run.owner_scope
        ref, _ = await ContextObjectStore(self._storage).put_result(
            run_id=run.run_id,
            activity_id=activity_id,
            scope_key=f"team:{scope.team_id}" if scope.team_id else f"user:{scope.user_id}",
            payload=payload,
        )
        return ref

    async def load_result(self, key: str) -> dict[str, JsonValue]:
        prefix = "execution/results/"
        if not key.startswith(prefix) or not key.endswith(".json"):
            raise ValueError("invalid Activity result reference")
        expected = key.rsplit("/", 1)[-1].removesuffix(".json")
        encoded = await self._storage.get_bytes(key)
        actual = hashlib.sha256(encoded).hexdigest()
        if actual != expected:
            raise ValueError("Activity result digest mismatch")
        payload = json.loads(encoded)
        if not isinstance(payload, dict):
            raise TypeError("Activity result must be a JSON object")
        if "_context_document" in payload:
            # Immutable capture/recording consumes the original preparation,
            # never a storage codec's private chunk addresses.
            return await self.load_context_document(key)
        if "_context_index" in payload:
            from app.domain.execution.context_index import OriginalContentIndex

            index = OriginalContentIndex.model_validate(payload["_context_index"])
            if key != f"execution/results/{index.activity_id}/{expected}.json":
                raise ValueError("CONTEXT_INDEX_REFERENCE_INVALID")
            # Full-content consumers (immutable capture) retain original JSON.
            # Context builders use load_result_bounded and read spans instead.
            original = await self._storage.get_bounded_bytes(
                index.original_ref, index.original_bytes
            )
            if (
                original.truncated
                or len(original.data) != index.original_bytes
                or hashlib.sha256(original.data).hexdigest() != index.original_digest
            ):
                raise ValueError("CONTEXT_SOURCE_INTEGRITY_INVALID")
            payload = json.loads(original.data)
            if not isinstance(payload, dict) or "_context_index" in payload:
                raise ValueError("CONTEXT_SOURCE_CONTENT_INVALID")
        return payload

    async def load_result_bounded(self, key: str, *, limit=262144):
        if not key.startswith("execution/results/") or not key.endswith(".json"):
            raise ValueError("CONTEXT_SOURCE_HANDLE_INVALID")
        expected = key.rsplit("/", 1)[-1].removesuffix(".json")
        if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
            raise ValueError("CONTEXT_SOURCE_HANDLE_INVALID")
        return await self.load_input_bounded(key=key, expected_digest=expected, limit=limit)

    async def put_context_document(self, activity_id, payload, *, run_id=None, scope_key=None):
        from app.domain.execution.context_index import ContextDocumentIndex, OriginalContentChunk

        encoded = self._encode(payload)
        if len(encoded) > 8388608:
            raise ValueError("CONTEXT_DOCUMENT_BYTES_EXCEEDED")
        compressed = zlib.compress(encoded) if len(encoded) > 4096 else encoded
        use_compression = len(compressed) < len(encoded) // 2
        if not use_compression and len(encoded) <= 65536:
            return await self.put_result(activity_id, payload, run_id=run_id, scope_key=scope_key)
        stored = compressed if use_compression else encoded
        chunks = []
        for offset in range(0, len(stored), 65536):
            body = stored[offset : offset + 65536]
            digest = hashlib.sha256(body).hexdigest()
            ref = f"execution/context_documents/{activity_id}/{digest}.bin"
            await put_execution_object(self._storage, ref, body, run_id=run_id, scope_key=scope_key)
            chunks.append(
                OriginalContentChunk(
                    offset=offset,
                    byte_length=len(body),
                    digest=digest,
                    object_ref=ref,
                )
            )
        index = ContextDocumentIndex(
            activity_id=activity_id,
            digest=hashlib.sha256(encoded).hexdigest(),
            byte_length=len(encoded),
            encoding="zlib-json-v1" if use_compression else "json",
            stored_bytes=len(stored),
            chunks=tuple(chunks),
        )
        return await self.put_result(
            activity_id,
            {"_context_document": index.model_dump(mode="json")},
            run_id=run_id,
            scope_key=scope_key,
        )

    async def load_context_document(self, key):
        from app.domain.execution.context_index import ContextDocumentIndex

        payload = await self.load_result_bounded(key, limit=65536)
        if "_context_document" not in payload:
            return payload
        index = ContextDocumentIndex.model_validate(payload["_context_document"])
        if not key.startswith(f"execution/results/{index.activity_id}/"):
            raise ValueError("CONTEXT_DOCUMENT_INDEX_INVALID")
        parts = []
        for chunk in index.chunks:
            try:
                part = await read_bounded(self._storage, chunk.object_ref, 65536)
            except ObjectNotFoundError as error:
                raise ValueError("CONTEXT_SOURCE_UNAVAILABLE") from error
            if (
                part.truncated
                or len(part.data) != chunk.byte_length
                or hashlib.sha256(part.data).hexdigest() != chunk.digest
            ):
                raise ValueError("CONTEXT_SOURCE_INTEGRITY_INVALID")
            parts.append(part.data)
        encoded = b"".join(parts)
        if index.encoding == "zlib-json-v1":
            decoder = zlib.decompressobj()
            try:
                # Never allocate beyond the declared, hard-capped original size.
                encoded = decoder.decompress(encoded, index.byte_length + 1)
            except zlib.error as error:
                raise ValueError("CONTEXT_SOURCE_INTEGRITY_INVALID") from error
            if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                raise ValueError("CONTEXT_SOURCE_INTEGRITY_INVALID")
        if len(encoded) != index.byte_length or hashlib.sha256(encoded).hexdigest() != index.digest:
            raise ValueError("CONTEXT_SOURCE_INTEGRITY_INVALID")
        payload = json.loads(encoded)
        if not isinstance(payload, dict) or payload.get("kind") not in {
            "context_prepare",
            "context_attachment",
        }:
            raise ValueError("CONTEXT_DOCUMENT_CONTENT_INVALID")
        return payload

    @staticmethod
    def _encode(payload: dict[str, JsonValue]) -> bytes:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()


__all__ = ["ActivityObjectStore"]
