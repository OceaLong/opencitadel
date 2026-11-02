"""Bounded history pages captured at admission, never rebuilt from mutable datasets."""

from app.domain.evaluation.dataset import ConversationMessage
from app.domain.execution.context_collection import ContextInputCollection, ContextInputPage
from app.domain.execution.context_models import (
    ContextMessage,
    ContextSource,
    ContextSourceRef,
    context_digest,
)
from app.domain.execution.serialization import canonical_json_bytes


class ContextInputCollectionStore:
    def __init__(self, objects):
        self.objects = objects
        # This adapter is constructed per admission/prepare/permit operation.
        # Cache only verified immutable pages, never requester authorization.
        self._pages = {}

    @staticmethod
    def bound(payload, *, run_id, scope_key):
        value = ContextInputCollection.model_validate(payload.get("_context_collection"))
        if value.run_id != run_id or value.scope_key != scope_key or payload.get("conversation"):
            raise ValueError("CONTEXT_COLLECTION_IDENTITY_CONFLICT")
        return value

    async def freeze(self, run_id, scope, history, policy):
        if not isinstance(history, (list, tuple)) or not 1 <= len(history) <= 4096:
            raise ValueError("CONTEXT_COLLECTION_LIMIT_EXCEEDED")
        pages, batch = [], []
        first = total = 0
        limit = min(policy.read_bytes, 65536)

        def body():
            return {"kind": "context_input_page", "first": first, "messages": batch}

        async def flush():
            nonlocal first, total, batch
            value = body()
            size = len(canonical_json_bytes(value))
            if size > limit or total + size > policy.prepare_bytes:
                raise ValueError("CONTEXT_COLLECTION_LIMIT_EXCEEDED")
            ref, digest = await self.objects.put_input(run_id, value, scope=scope)
            pages.append(
                ContextInputPage(
                    first=first, count=len(batch), object_ref=ref, digest=digest, byte_length=size
                )
            )
            first += len(batch)
            total += size
            batch = []
            if len(pages) > 128:
                raise ValueError("CONTEXT_COLLECTION_LIMIT_EXCEEDED")

        for raw in history:
            message = ConversationMessage.model_validate(raw).model_dump(mode="json")
            # A page has complete messages, not partial JSON or split tool arguments.
            batch.append(message)
            if len(batch) > 200 or len(canonical_json_bytes(body())) > limit:
                batch.pop()
                if batch:
                    await flush()
                batch.append(message)
                if len(canonical_json_bytes(body())) > limit:
                    raise ValueError("CONTEXT_COLLECTION_MESSAGE_TOO_LARGE")
        if batch:
            await flush()
        return ContextInputCollection(
            run_id=run_id,
            scope_key=f"team:{scope.team_id}" if scope.team_id else f"user:{scope.user_id}",
            count=len(history),
            byte_length=total,
            pages=tuple(pages),
        )

    async def page(self, collection, page, *, limit):
        if page not in collection.pages or page.byte_length > limit:
            raise ValueError("CONTEXT_COLLECTION_INDEX_INVALID")
        if page in self._pages:
            return self._pages[page]
        value = await self.objects.load_input_bounded(
            key=page.object_ref, expected_digest=page.digest, limit=limit
        )
        if (
            value.get("kind") != "context_input_page"
            or value.get("first") != page.first
            or len(canonical_json_bytes(value)) != page.byte_length
        ):
            raise ValueError("CONTEXT_COLLECTION_INDEX_INVALID")
        raw = value.get("messages")
        if not isinstance(raw, list) or len(raw) != page.count:
            raise ValueError("CONTEXT_COLLECTION_INDEX_INVALID")
        messages = tuple(ConversationMessage.model_validate(item) for item in raw)
        self._pages[page] = messages
        return messages

    @staticmethod
    def source(collection, ordinal, message, *, epoch):
        raw = message.content.encode()
        end = min(len(raw), 8192)
        while end < len(raw) and raw[end] & 0xC0 == 0x80:
            end -= 1
        ref = ContextSourceRef(
            source_id=f"collection:{collection.run_id}:{ordinal:04d}",
            scope_key=collection.scope_key,
            run_id=collection.run_id,
            kind=message.role,
            digest=context_digest(message.model_dump(mode="json")),
            formal_position=0,
            authorization_epoch=epoch,
        )
        value = ContextMessage(role=message.role, content=raw[:end].decode())
        return ContextSource(
            ref=ref,
            messages=(value,),
            messages_digest=context_digest([value.model_dump(mode="json")]),
        )

    async def resolve(self, collection, source, *, limit, epoch):
        try:
            prefix, identity, ordinal = source.source_id.split(":")
            position = int(ordinal)
            if (
                prefix != "collection"
                or identity != str(collection.run_id)
                or ordinal != f"{position:04d}"
                or not 0 <= position < collection.count
            ):
                raise ValueError
        except (ValueError, AttributeError) as error:
            raise ValueError("CONTEXT_SOURCE_HANDLE_INVALID") from error
        page = next(
            item for item in collection.pages if item.first <= position < item.first + item.count
        )
        message = (await self.page(collection, page, limit=limit))[position - page.first]
        if self.source(collection, position, message, epoch=epoch).ref != source:
            raise ValueError("CONTEXT_SOURCE_IDENTITY_CONFLICT")
        return message
