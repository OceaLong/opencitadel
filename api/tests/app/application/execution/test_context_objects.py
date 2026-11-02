from uuid import uuid4

import pytest

from app.application.execution.context_objects import ContextObjectStore
from app.domain.external.object_storage import BoundedObjectBytes, ObjectNotFoundError

pytestmark = pytest.mark.asyncio


class Storage:
    def __init__(self):
        self.objects = {}
        self.reads = []

    async def put_bytes(self, key, body):
        self.objects[key] = body

    async def get_bounded_bytes(self, key, limit):
        self.reads.append((key, limit))
        if key not in self.objects:
            raise ObjectNotFoundError(key)
        body = self.objects[key]
        return BoundedObjectBytes(data=body[:limit], truncated=len(body) > limit)

    async def get_bytes(self, key):
        raise AssertionError("indexed readers must not fall back to a whole-object read")


async def allow(index):
    return None


async def test_large_chinese_result_is_indexed_and_read_in_legal_bounded_utf8_spans():
    storage = Storage()
    store = ContextObjectStore(storage)
    run_id, activity_id = uuid4(), uuid4()
    text = "中文🙂精确事实" * 20000
    ref, index = await store.put_result(
        run_id=run_id,
        activity_id=activity_id,
        scope_key="user:source-test",
        payload={
            "kind": "tool",
            "message": {"role": "tool", "tool_call_id": "call-1", "content": text},
        },
    )
    loaded = await store.load_index(
        ref=ref, digest=ref.rsplit("/", 1)[-1][:-5], scope_key="user:source-test", run_id=run_id
    )
    assert loaded == index
    assert index.message_metadata["tool_call_id"] == "call-1"
    assert len(index.chunks) > 1
    cursor = 0
    parts = []
    while cursor is not None:
        body, cursor = await store.read_span(index, offset=cursor, limit=65536, before_read=allow)
        assert len(body) <= 65536
        parts.append(body.decode())
    assert "".join(parts) == text
    assert all(limit == 65536 for _key, limit in storage.reads)
    assert not any(key == index.original_ref for key, _limit in storage.reads)


async def test_tampered_chunk_and_revoked_authority_are_not_replaced_by_summary():
    storage = Storage()
    store = ContextObjectStore(storage)
    _ref, index = await store.put_result(
        run_id=uuid4(),
        activity_id=uuid4(),
        scope_key="user:source-test",
        payload={"kind": "model", "message": {"role": "assistant", "content": "fact" * 20000}},
    )

    async def revoked(_index):
        raise ValueError("CONTEXT_SOURCE_AUTHORIZATION_REVOKED")

    with pytest.raises(ValueError, match="REVOKED"):
        await store.read_span(index, before_read=revoked)
    assert storage.reads == []
    chunk = index.chunks[0]
    storage.objects[chunk.object_ref] = b"tampered"
    with pytest.raises(ValueError, match="INTEGRITY_INVALID"):
        await store.read_span(index, before_read=allow)
    del storage.objects[chunk.object_ref]
    with pytest.raises(ValueError, match="SOURCE_UNAVAILABLE"):
        await store.read_span(index, before_read=allow)


async def test_index_identity_scope_and_complete_tool_arguments_are_fixed():
    storage = Storage()
    store = ContextObjectStore(storage)
    run = uuid4()
    call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "lookup", "arguments": '{"exact":"值"}'},
    }
    ref, index = await store.put_result(
        run_id=run,
        activity_id=uuid4(),
        scope_key="user:source-test",
        payload={
            "kind": "model",
            "message": {"role": "assistant", "content": "long " * 20000, "tool_calls": [call]},
        },
    )
    assert (
        index.message_metadata["tool_calls"][0]["function"]["arguments"]
        == call["function"]["arguments"]
    )
    with pytest.raises(ValueError, match="SCOPE_MISMATCH"):
        await store.load_index(
            ref=ref, digest=ref.rsplit("/", 1)[-1][:-5], scope_key="team:foreign", run_id=run
        )
    with pytest.raises(ValueError, match="LIMIT_INVALID"):
        await store.read_span(index, limit=262145, before_read=allow)


async def test_context_result_producer_keeps_bounded_index_and_exact_full_capture():
    from app.application.execution.activity_inputs import ActivityObjectStore
    from app.application.execution.run_context import run_execution_context
    from app.domain.execution.run import RunFamily
    from tests.app.application.execution.test_context_decisions import state_for

    class CaptureStorage(Storage):
        async def get_bytes(self, key):
            return self.objects[key]

    storage = CaptureStorage()
    objects = ActivityObjectStore(storage)
    run = run_execution_context(state_for(RunFamily.AGENT))
    activity = uuid4()
    payload = {
        "kind": "tool",
        "message": {"role": "tool", "tool_call_id": "call-1", "content": "完整原文🙂" * 20000},
    }
    ref = await objects.put_context_result(activity, payload, run=run)
    bounded = await objects.load_result_bounded(ref, limit=65536)
    assert "_context_index" in bounded
    assert bounded["_context_index"]["message_metadata"]["tool_call_id"] == "call-1"
    assert await objects.load_result(ref) == payload
    index = bounded["_context_index"]
    storage.objects[index["original_ref"]] = b'{"message":"tampered"}'
    with pytest.raises(ValueError, match="INTEGRITY_INVALID"):
        await objects.load_result(ref)


async def test_indexed_tool_excerpt_is_complete_json_with_original_protocol_identity():
    import json
    from types import SimpleNamespace

    from app.application.execution.activity_inputs import ActivityObjectStore
    from app.infrastructure.execution.context_activity_sources import ContextActivitySources

    storage = Storage()
    objects = ActivityObjectStore(storage)
    run, activity = uuid4(), uuid4()
    ref, index = await ContextObjectStore(storage).put_result(
        run_id=run,
        activity_id=activity,
        scope_key="user:source-test",
        payload={
            "kind": "tool",
            "message": {
                "role": "tool",
                "tool_call_id": "exact-call-id",
                "content": json.dumps(
                    {"success": True, "data": "事实🙂" * 20000}, ensure_ascii=False
                ),
            },
        },
    )
    state = SimpleNamespace(
        run_id=run,
        requested_activities=((activity, "tool.call", 0),),
        settled_activities=((activity, "succeeded", 0),),
        activity_results=((activity, 0, ref, None, None),),
    )

    async def before_read():
        pass

    value = await ContextActivitySources(objects, storage).read(
        state,
        activity,
        0,
        scope_key="user:source-test",
        epoch="epoch-1",
        limit=65536,
        before_read=before_read,
    )
    body = json.loads(value.message.content)
    assert set(body) == {"_context_excerpt"}
    assert body["_context_excerpt"]["source_id"] == value.ref.source_id
    assert body["_context_excerpt"]["total_bytes"] == index.content_bytes
    assert value.message.tool_call_id == "exact-call-id"
    assert value.ref.local_sequence == 1
    assert all(key != index.original_ref for key, _ in storage.reads)


async def test_large_context_document_reads_bounded_chunks_and_rejects_tampering():
    from app.application.execution.activity_inputs import ActivityObjectStore

    storage = Storage()
    objects = ActivityObjectStore(storage)
    payload = {"kind": "context_prepare", "compilation": {"sources": ["证据🙂" * 1000] * 200}}
    ref = await objects.put_context_document(uuid4(), payload)
    assert await objects.load_context_document(ref) == payload
    assert all(limit <= 65536 for _, limit in storage.reads)
    index = (await objects.load_result_bounded(ref))["_context_document"]
    assert index["encoding"] == "zlib-json-v1"
    assert index["stored_bytes"] < index["byte_length"] // 2
    storage.objects[index["chunks"][-1]["object_ref"]] = b"corrupted"
    with pytest.raises(ValueError, match="INTEGRITY_INVALID"):
        await objects.load_context_document(ref)


async def test_context_document_rejects_cross_activity_index_and_over_limit_before_writes():
    from app.application.execution.activity_inputs import ActivityObjectStore

    storage = Storage()
    objects = ActivityObjectStore(storage)
    payload = {"kind": "context_prepare", "compilation": {"body": "x" * 100000}}
    ref = await objects.put_context_document(uuid4(), payload)
    index = await objects.load_result_bounded(ref)
    forged = await objects.put_result(uuid4(), index)
    with pytest.raises(ValueError, match="INDEX_INVALID"):
        await objects.load_context_document(forged)
    written = len(storage.objects)
    with pytest.raises(ValueError, match="BYTES_EXCEEDED"):
        await objects.put_context_document(
            uuid4(), {"kind": "context_prepare", "body": "x" * 8388608}
        )
    assert len(storage.objects) == written


@pytest.mark.parametrize("damage", ["declared_size", "trailing_stream", "truncated_stream"])
async def test_compressed_document_rejects_bombs_and_noncanonical_streams(damage):
    import hashlib
    import zlib

    from app.application.execution.activity_inputs import ActivityObjectStore

    storage = Storage()
    objects = ActivityObjectStore(storage)
    identity = uuid4()
    ref = await objects.put_context_document(
        identity, {"kind": "context_prepare", "body": "x" * 200000}
    )
    body = await objects.load_result_bounded(ref)
    index = body["_context_document"]
    assert len(index["chunks"]) == 1
    if damage == "declared_size":
        index["byte_length"] = 16
    else:
        chunk = index["chunks"][0]
        encoded = storage.objects[chunk["object_ref"]]
        encoded = encoded + zlib.compress(b"extra") if damage == "trailing_stream" else encoded[:-1]
        digest = hashlib.sha256(encoded).hexdigest()
        key = f"execution/context_documents/{identity}/{digest}.bin"
        storage.objects[key] = encoded
        chunk.update(digest=digest, object_ref=key, byte_length=len(encoded))
        index["stored_bytes"] = len(encoded)
    forged = await objects.put_result(identity, body)
    with pytest.raises(ValueError, match="INTEGRITY_INVALID"):
        await objects.load_context_document(forged)


async def test_legacy_uncompressed_context_document_remains_readable():
    import hashlib

    from app.application.execution.activity_inputs import ActivityObjectStore

    storage = Storage()
    objects = ActivityObjectStore(storage)
    identity = uuid4()
    payload = {"kind": "context_prepare", "body": "legacy"}
    raw = objects._encode(payload)
    digest = hashlib.sha256(raw).hexdigest()
    key = f"execution/context_documents/{identity}/{digest}.bin"
    storage.objects[key] = raw
    ref = await objects.put_result(
        identity,
        {
            "_context_document": {
                "format_version": 1,
                "activity_id": str(identity),
                "digest": digest,
                "byte_length": len(raw),
                "chunks": [
                    {"offset": 0, "byte_length": len(raw), "digest": digest, "object_ref": key}
                ],
            }
        },
    )
    assert await objects.load_context_document(ref) == payload


async def test_complete_capture_expands_context_document_instead_of_capturing_chunk_addresses():
    from app.application.execution.activity_inputs import ActivityObjectStore

    class CaptureStorage(Storage):
        async def get_bytes(self, key):
            return self.objects[key]

    storage = CaptureStorage()
    objects = ActivityObjectStore(storage)
    payload = {"kind": "context_prepare", "compilation": {"body": "exact evidence " * 10000}}
    ref = await objects.put_context_document(uuid4(), payload)
    assert "_context_document" in await objects.load_result_bounded(ref)
    assert await objects.load_result(ref) == payload


async def test_compressed_attachment_document_keeps_its_kind_and_exact_media():
    from app.application.execution.activity_inputs import ActivityObjectStore

    storage = Storage()
    objects = ActivityObjectStore(storage)
    payload = {
        "kind": "context_attachment",
        "file_id": "picture",
        "media": {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64," + "a" * 16000},
        },
    }
    ref = await objects.put_context_document(uuid4(), payload)
    index = (await objects.load_result_bounded(ref, limit=65536))["_context_document"]
    assert index["encoding"] == "zlib-json-v1"
    assert await objects.load_context_document(ref) == payload
    assert all(limit <= 65536 for _, limit in storage.reads)
