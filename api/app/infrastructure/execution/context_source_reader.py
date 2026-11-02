"""Private reads require formal identity before any bounded object-store I/O."""

import hashlib
import json
from uuid import UUID

from sqlalchemy import select

from app.application.execution.context_io import read_bounded
from app.application.execution.context_objects import ContextObjectStore
from app.domain.execution.context_index import OriginalContentIndex
from app.domain.execution.context_models import (
    ContextMessage,
    ContextSource,
    ContextSourceRef,
    context_digest,
)
from app.domain.execution.store import verify_event_hashes
from app.domain.external.object_storage import ObjectNotFoundError
from app.infrastructure.execution.models import ExecutionEventORM
from app.infrastructure.execution.postgres_event_store import PostgresEventStore


class ContextSourceReader:
    def __init__(self, session, storage, *, authorizer):
        self.session = session
        self.storage = storage
        self.authorizer = authorizer

    async def _load(self, cut, descriptor, *, limit):
        if type(limit) is not int or not 1 <= limit <= 262144:
            raise ValueError("CONTEXT_READ_LIMIT_INVALID")
        if not descriptor.source_id.startswith("formal:"):
            raise ValueError("CONTEXT_SOURCE_HANDLE_INVALID")
        authorization_epoch = await self.authorizer(cut, descriptor)
        if not isinstance(authorization_epoch, str) or not authorization_epoch:
            raise ValueError("CONTEXT_SOURCE_AUTHORIZATION_UNAVAILABLE")
        identity = UUID(descriptor.source_id.removeprefix("formal:"))
        event = await self.session.scalar(
            select(ExecutionEventORM).where(
                ExecutionEventORM.event_id == identity,
                ExecutionEventORM.owner_scope_key == cut.scope_key,
                ExecutionEventORM.stream_type == "run",
                ExecutionEventORM.stream_id == str(descriptor.run_id),
                ExecutionEventORM.position == descriptor.formal_position,
                ExecutionEventORM.position <= cut.through_position,
            )
        )
        if event is None:
            raise ValueError("CONTEXT_SOURCE_UNAVAILABLE")
        origin = await self.session.scalar(
            select(ExecutionEventORM).where(
                ExecutionEventORM.stream_type == "run",
                ExecutionEventORM.stream_id == event.stream_id,
                ExecutionEventORM.stream_version == 1,
                ExecutionEventORM.owner_scope_key == cut.scope_key,
            )
        )
        if origin is None:
            raise ValueError("CONTEXT_SOURCE_UNAVAILABLE")
        verify_event_hashes(tuple(PostgresEventStore._to_stored(row) for row in (origin, event)))
        if (
            event.event_hash != descriptor.event_hash
            or origin.public_payload.get("source_entity_type") != "session"
            or origin.public_payload.get("source_entity_id") != cut.collection_id
        ):
            raise ValueError("CONTEXT_SOURCE_BOUNDARY_MISMATCH")
        if descriptor.kind == "user" and event.event_type == "RunCreated":
            semantic = event.internal_payload.get("semantic_payload", {})
            key, digest = semantic.get("input_ref"), semantic.get("input_digest")
        elif descriptor.kind == "assistant" and event.event_type == "RunCompleted":
            key = event.public_payload.get("result_ref")
            digest = key.rsplit("/", 1)[-1].removesuffix(".json") if isinstance(key, str) else None
        else:
            raise ValueError("CONTEXT_SOURCE_IDENTITY_CONFLICT")
        if (key, digest) != (descriptor.object_ref, descriptor.object_digest):
            raise ValueError("CONTEXT_SOURCE_IDENTITY_CONFLICT")
        try:
            bounded = await read_bounded(self.storage, key, limit)
        except ObjectNotFoundError as error:
            raise ValueError("CONTEXT_SOURCE_UNAVAILABLE") from error
        if bounded.truncated:
            raise ValueError("CONTEXT_SOURCE_REQUIRES_INDEXED_EXCERPT")
        if hashlib.sha256(bounded.data).hexdigest() != digest:
            raise ValueError("CONTEXT_SOURCE_INTEGRITY_INVALID")
        body = json.loads(bounded.data)
        if not isinstance(body, dict):
            raise ValueError("CONTEXT_SOURCE_CONTENT_INVALID")  # noqa: TRY004 - stable failure code
        return body, authorization_epoch

    def _index(self, cut, descriptor, body):
        index = OriginalContentIndex.model_validate(body["_context_index"])
        if (
            descriptor.object_ref
            != f"execution/results/{index.activity_id}/{descriptor.object_digest}.json"
        ):
            raise ValueError("CONTEXT_INDEX_REFERENCE_INVALID")
        if index.scope_key != cut.scope_key or index.run_id != descriptor.run_id:
            raise ValueError("CONTEXT_SOURCE_SCOPE_MISMATCH")
        if (
            descriptor.kind != "assistant"
            or index.message_metadata.get("role") != "assistant"
            or index.message_metadata.get("tool_calls")
        ):
            raise ValueError("CONTEXT_SOURCE_PROTOCOL_INVALID")
        return index

    @staticmethod
    def _text(descriptor, body):
        if descriptor.kind == "user":
            text = body.get("message")
        else:
            message = body.get("message")
            if (
                not isinstance(message, dict)
                or message.get("role") != "assistant"
                or message.get("tool_calls")
            ):
                raise ValueError("CONTEXT_SOURCE_PROTOCOL_INVALID")
            text = message.get("content")
        if not isinstance(text, str):
            raise ValueError("CONTEXT_SOURCE_CONTENT_INVALID")  # noqa: TRY004 - stable failure code
        return text

    async def read_span(self, cut, descriptor, *, offset=0, limit=8192):
        if (
            type(offset) is not int
            or offset < 0
            or type(limit) is not int
            or not 4 <= limit <= 65536
        ):
            raise ValueError("CONTEXT_READ_LIMIT_INVALID")
        body, epoch = await self._load(cut, descriptor, limit=262144)

        async def current_authority(_index):
            if await self.authorizer(cut, descriptor) != epoch:
                raise ValueError("CONTEXT_SOURCE_AUTHORIZATION_REVOKED")

        if "_context_index" in body:
            index = self._index(cut, descriptor, body)
            data, following = await ContextObjectStore(self.storage).read_span(
                index, offset=offset, limit=limit, before_read=current_authority
            )
            total = index.content_bytes
        else:
            content = self._text(descriptor, body).encode()
            if offset > len(content):
                raise ValueError("CONTEXT_READ_LIMIT_INVALID")
            end = min(len(content), offset + limit)
            while end < len(content) and content[end] & 0xC0 == 0x80:
                end -= 1
            data, total = content[offset:end], len(content)
            following = end if end < total else None
        await current_authority(None)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("CONTEXT_READ_UTF8_BOUNDARY_INVALID") from error
        return text, following, total

    async def read(self, cut, descriptor, *, limit=65536):
        body, authorization_epoch = await self._load(cut, descriptor, limit=limit)
        if "_context_index" in body:
            index = self._index(cut, descriptor, body)

            async def current_authority(_index):
                if await self.authorizer(cut, descriptor) != authorization_epoch:
                    raise ValueError("CONTEXT_SOURCE_AUTHORIZATION_REVOKED")

            data, next_offset = await ContextObjectStore(self.storage).read_span(
                index, limit=min(limit, 8192), before_read=current_authority
            )
            text = data.decode()
            if next_offset is not None:
                text += f"\n[Original source {descriptor.source_id}: excerpt bytes 0..{next_offset} of {index.content_bytes}; remaining content requires read_source.]"
        else:
            text = self._text(descriptor, body)
        if await self.authorizer(cut, descriptor) != authorization_epoch:
            raise ValueError("CONTEXT_SOURCE_AUTHORIZATION_REVOKED")
        messages = (ContextMessage(role=descriptor.kind, content=text),)
        return ContextSource(
            ref=ContextSourceRef(
                source_id=descriptor.source_id,
                scope_key=cut.scope_key,
                run_id=descriptor.run_id,
                kind=descriptor.kind,
                digest=descriptor.object_digest,
                formal_position=descriptor.formal_position,
                authorization_epoch=authorization_epoch,
            ),
            messages=messages,
            messages_digest=context_digest(
                [message.model_dump(mode="json") for message in messages]
            ),
        )
