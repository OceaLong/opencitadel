"""Resolve original text only through the consuming prepared manifest."""

from app.application.execution.context_collection import ContextInputCollectionStore
from app.application.execution.context_objects import ContextObjectStore
from app.application.execution.context_obligations import effect_obligations
from app.domain.execution.context_index import OriginalContentIndex
from app.domain.execution.context_models import AcceptedContextCut
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.context_activity_sources import ContextActivitySources
from app.infrastructure.execution.context_source_descriptors import ContextSourceDescriptors
from app.infrastructure.execution.context_source_reader import ContextSourceReader
from app.infrastructure.execution.verified_run_loader import PostgresVerifiedRunLoader


class ContextOriginalReader:
    def __init__(self, *, uow_factory, sources, objects, storage):
        self.uow_factory, self.sources = uow_factory, sources
        self.objects, self.storage = objects, storage

    async def read_source_span(self, manifest, source, *, offset, limit):
        async with self.uow_factory(AuthorizationContext.system("execution-kernel")) as work:
            return await self.read_source_span_in_uow(
                work, manifest, source, offset=offset, limit=limit
            )

    async def read_source_span_in_uow(self, work, manifest, source, *, offset, limit):
        """Prepare-time lookup shares the caller's authorization locks and read budget."""
        read = await self.bind_in_uow(work, manifest)
        return await read(source, offset=offset, limit=limit)

    async def bind_in_uow(self, work, manifest):
        """Authorize one bounded source set for this transaction, never a global cache."""
        boundary = manifest.boundary
        await self.sources.validate_in_uow(work, boundary, manifest.original_closure)
        authority = await self.sources.open(work, boundary)
        transaction = work.db_session.get_transaction()
        refs = {ref.source_id: ref for ref in manifest.original_closure}
        states = {}

        async def read(source, *, offset, limit):
            if transaction is None or not transaction.is_active:
                raise ValueError("CONTEXT_SOURCE_AUTHORIZATION_REVOKED")
            if refs.get(source.source_id) != source:
                raise ValueError("CONTEXT_SOURCE_IDENTITY_CONFLICT")
            return await self._read_bound(
                work,
                manifest,
                source,
                offset=offset,
                limit=limit,
                authority=authority,
                states=states,
            )

        return read

    async def _read_bound(self, work, manifest, source, *, offset, limit, authority, states):
        boundary = manifest.boundary
        scope, epoch, state, payload = authority

        async def live(*_args):
            return epoch  # Principal/membership locks remain owned by this UoW.

        if source.source_id.startswith("formal:"):
            descriptor = await ContextSourceDescriptors(work.db_session).resolve(boundary, source)
            cut = AcceptedContextCut(
                scope_key=boundary.scope_key,
                collection_id=boundary.collection_id,
                through_position=boundary.through_position,
            )
            return await ContextSourceReader(
                work.db_session, self.storage, authorizer=live
            ).read_span(cut, descriptor, offset=offset, limit=limit)
        if source.run_id != state.run_id:
            key = (source.run_id, not source.source_id.startswith("effects:"))
            if key not in states:
                states[key] = await self.sources.source_state(
                    work, scope, boundary, source.run_id, require_completed=key[1]
                )
            state = states[key]
        if source.source_id == f"input:{state.run_id}":
            payload = await self.objects.load_input_bounded(
                key=state.semantic_payload["input_ref"],
                expected_digest=source.digest,
                limit=manifest.policy.read_bytes,
            )
            content = payload.get("message")
        elif source.source_id.startswith("collection:"):
            payload = await self.objects.load_input_bounded(
                key=state.semantic_payload["input_ref"],
                expected_digest=state.semantic_payload["input_digest"],
                limit=manifest.policy.read_bytes,
            )
            store = ContextInputCollectionStore(self.objects)
            collection = store.bound(payload, run_id=state.run_id, scope_key=boundary.scope_key)
            message = await store.resolve(
                collection, source, limit=manifest.policy.read_bytes, epoch=epoch
            )
            content = message.content
        elif source.source_id.startswith("attachment:"):
            from app.infrastructure.execution.context_file_authority import (
                attachment_index,
                read_attachment_span,
                validate_context_files,
            )

            payload = await self.objects.load_input_bounded(
                key=state.semantic_payload["input_ref"],
                expected_digest=state.semantic_payload["input_digest"],
                limit=manifest.policy.read_bytes,
            )
            files = await validate_context_files(work.db_session, scope, payload)
            matches = [
                item
                for item in files
                if source.source_id == f"attachment:{state.run_id}:{item.file_id}"
            ]
            if len(matches) != 1 or matches[0].representation != "text":
                raise ValueError("CONTEXT_SOURCE_KIND_UNSUPPORTED")
            index = await attachment_index(
                self.objects, matches[0], run_id=state.run_id, scope_key=boundary.scope_key
            )
            return await read_attachment_span(
                self.objects, index, offset=offset, limit=limit, before_read=live
            )
        elif source.source_id.startswith("effects:"):
            fixed = (
                await PostgresVerifiedRunLoader(work.db_session).load(
                    source.run_id,
                    owner_scope=(None if scope.team_id else scope.user_id, scope.team_id),
                    at_version=int(source.source_id.rsplit(":", 1)[-1]),
                )
            ).replay.state
            obligations = effect_obligations(fixed, scope_key=boundary.scope_key, epoch=epoch)
            if obligations is None or obligations.ref != source:
                raise ValueError("CONTEXT_SOURCE_IDENTITY_CONFLICT")
            content = obligations.messages[0].content
        elif source.source_id.startswith("activity:"):
            identity, generation = ContextActivitySources.identity(source)
            ref = next(
                (
                    item[2]
                    for item in state.activity_results
                    if item[0] == identity and item[1] == generation
                ),
                None,
            )
            if ref is None:
                raise ValueError("CONTEXT_SOURCE_UNAVAILABLE")
            body = await self.objects.load_result_bounded(ref, limit=manifest.policy.read_bytes)
            if "_context_index" in body:
                index = OriginalContentIndex.model_validate(body["_context_index"])
                if (
                    index.scope_key != boundary.scope_key
                    or index.run_id != source.run_id
                    or index.activity_id != identity
                    or ref != f"execution/results/{identity}/{source.digest}.json"
                ):
                    raise ValueError("CONTEXT_SOURCE_IDENTITY_CONFLICT")
                data, following = await ContextObjectStore(self.storage).read_span(
                    index, offset=offset, limit=limit, before_read=live
                )
                return data.decode(), following, index.content_bytes
            content = body.get("message", {}).get("content")
        else:
            raise ValueError("CONTEXT_SOURCE_KIND_UNSUPPORTED")
        if not isinstance(content, str):
            raise ValueError("CONTEXT_SOURCE_CONTENT_INVALID")  # noqa: TRY004 - stable failure code
        raw = content.encode()
        if type(offset) is not int or offset < 0 or offset > len(raw):
            raise ValueError("CONTEXT_READ_LIMIT_INVALID")
        end = min(offset + limit, len(raw))
        while end < len(raw) and raw[end] & 0xC0 == 0x80:
            end -= 1
        try:
            result = raw[offset:end].decode()
        except UnicodeDecodeError as error:
            raise ValueError("CONTEXT_READ_UTF8_BOUNDARY_INVALID") from error
        return result, end if end < len(raw) else None, len(raw)
