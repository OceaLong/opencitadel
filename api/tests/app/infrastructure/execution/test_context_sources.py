# ruff: noqa: F401,F811 -- pytest fixture imports
"""Private history references from formal facts with a stable, scoped cut."""

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.execution.events import NewEvent
from app.domain.execution.store import AppendContext, StreamRef
from app.domain.external.object_storage import BoundedObjectBytes
from app.domain.models.authorization import AuthorizationContext
from app.infrastructure.execution.context_source_reader import ContextSourceReader
from app.infrastructure.execution.postgres_context_sources import PostgresContextSources
from app.infrastructure.execution.postgres_event_store import PostgresEventStore
from app.infrastructure.security.db_authorization import configure_session_authorization
from tests.app.execution_test_support import run_policy_snapshot_json
from tests.app.infrastructure.execution.test_definition_lifecycle import (
    datasets,
    definition_runtime,
    fresh_f07_database,
    isolated_database,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


async def seed(sessions, owner, collection, count, *, indexed_answers=False):
    objects = {}
    from app.application.execution.context_objects import ContextObjectStore

    class Writer:
        async def put_bytes(self, key, body):
            objects[key] = body

    async with sessions() as db:
        await configure_session_authorization(db, AuthorizationContext.system("execution-kernel"))
        store = PostgresEventStore(db)
        for _ in range(count):
            run_id = uuid4()
            user_body = json.dumps({"message": f"private question {run_id}"}).encode()
            user_digest = hashlib.sha256(user_body).hexdigest()
            user_key = f"execution/inputs/{run_id}/{user_digest}.json"
            answer_body = json.dumps(
                {"message": {"role": "assistant", "content": f"private answer {run_id}"}}
            ).encode()
            answer_digest = hashlib.sha256(answer_body).hexdigest()
            answer_key = f"execution/results/{uuid4()}/{answer_digest}.json"
            objects[user_key] = user_body
            objects[answer_key] = answer_body
            if indexed_answers:
                answer_key, _index = await ContextObjectStore(Writer()).put_result(
                    run_id=run_id,
                    activity_id=uuid4(),
                    scope_key=f"user:{owner}",
                    payload={"message": {"role": "assistant", "content": "原始精确事实" * 20000}},
                )
            await store.append(
                StreamRef(stream_type="run", stream_id=str(run_id)),
                0,
                (
                    NewEvent(
                        event_type="RunCreated",
                        event_schema_version=3,
                        public_payload={
                            "family": "ask",
                            "source_entity_type": "session",
                            "source_entity_id": collection,
                            "parent_run_id": None,
                            "input": {},
                        },
                        internal_payload={
                            "semantic_payload": {
                                "input_ref": user_key,
                                "input_digest": user_digest,
                            },
                            "policy_snapshot": run_policy_snapshot_json("ask"),
                            "definition_pin": None,
                            "definition_manifest": None,
                        },
                    ),
                    NewEvent(
                        event_type="RunStarted",
                        event_schema_version=1,
                        public_payload={},
                        internal_payload={},
                    ),
                    NewEvent(
                        event_type="RunCompleted",
                        event_schema_version=1,
                        public_payload={"result_ref": answer_key},
                        internal_payload={},
                    ),
                ),
                AppendContext(
                    owner_user_id=owner,
                    team_id=None,
                    correlation_id=run_id,
                    causation_id=uuid4(),
                    occurred_at=datetime.now(UTC),
                ),
            )
        await db.commit()
    return objects


async def test_formal_catalog_pages_all_150_private_message_refs_and_freezes_cut(
    definition_runtime,
):
    service, scope, principal, sessions = definition_runtime
    collection = str(uuid4())
    await seed(sessions, principal.user_id, collection, 75)
    auth = AuthorizationContext.for_principal(principal, scope=scope)
    request = uuid4()
    async with service.uow_factory(auth) as work:
        catalog = PostgresContextSources(work.db_session, signing_secret="isolated-context-cursor")
        cut = await catalog.freeze(scope, collection, request_id=request)
        await work.commit()
    async with service.uow_factory(auth) as work:
        catalog = PostgresContextSources(work.db_session, signing_secret="isolated-context-cursor")
        first = await catalog.page(cut, limit=37)
    await seed(sessions, principal.user_id, collection, 1)
    values = list(first.sources)
    cursor = first.next_cursor
    async with service.uow_factory(auth) as work:
        catalog = PostgresContextSources(work.db_session, signing_secret="isolated-context-cursor")
        while cursor is not None:
            page = await catalog.page(cut, cursor=cursor, limit=37)
            values.extend(page.sources)
            cursor = page.next_cursor
        assert len(values) == 150
        assert [item.kind for item in values] == ["user", "assistant"] * 75
        assert len({item.source_id for item in values}) == 150
        assert all(item.formal_position <= cut.through_position for item in values)
        current = await catalog.freeze(scope, collection)
        assert await catalog.freeze(scope, collection, request_id=request) == cut
        with pytest.raises(ValueError, match="boundary"):
            await catalog.page(current, cursor=first.next_cursor)
        with pytest.raises(ValueError, match="boundary"):
            await catalog.page(
                cut.model_copy(update={"collection_id": "different"}), cursor=first.next_cursor
            )
        with pytest.raises(ValueError, match="limit"):
            await catalog.page(cut, limit=201)


async def test_foreign_scope_catalog_is_empty_under_actual_api_rls(definition_runtime):
    service, scope, principal, sessions = definition_runtime
    collection = str(uuid4())
    await seed(sessions, str(uuid4()), collection, 1)
    async with service.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        catalog = PostgresContextSources(work.db_session, signing_secret="isolated-context-cursor")
        cut = await catalog.freeze(scope, collection)
        assert (await catalog.page(cut)).sources == ()


async def test_private_reader_checks_formal_identity_digest_and_current_authority_before_io(
    definition_runtime,
):
    service, scope, principal, sessions = definition_runtime
    collection = str(uuid4())
    objects = await seed(sessions, principal.user_id, collection, 1)
    async with service.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        catalog = PostgresContextSources(work.db_session, signing_secret="isolated-context-cursor")
        cut = await catalog.freeze(scope, collection)
        descriptor = (await catalog.page(cut)).sources[0]
    calls = []

    class Storage:
        async def get_bounded_bytes(self, key, limit):
            calls.append((key, limit))
            body = objects[key]
            return BoundedObjectBytes(data=body[:limit], truncated=len(body) > limit)

    async def authorize(_cut, _source):
        return "current-epoch"

    async def revoked(_cut, _source):
        raise ValueError("CONTEXT_SOURCE_AUTHORIZATION_REVOKED")

    async with sessions() as db:
        await configure_session_authorization(db, AuthorizationContext.system("execution-kernel"))
        reader = ContextSourceReader(db, Storage(), authorizer=authorize)
        source = await reader.read(cut, descriptor, limit=1024)
        assert source.ref.digest == descriptor.object_digest
        assert source.messages[0].content.startswith("private question")
        assert calls == [(descriptor.object_ref, 1024)]
        calls.clear()
        forged = descriptor.model_copy(update={"object_ref": "execution/inputs/guessed.json"})
        with pytest.raises(ValueError, match="IDENTITY_CONFLICT"):
            await reader.read(cut, forged)
        assert calls == []
        reader.authorizer = revoked
        with pytest.raises(ValueError, match="REVOKED"):
            await reader.read(cut, descriptor)
        assert calls == []
        reader.authorizer = authorize
        objects[descriptor.object_ref] = b'{"message":"tampered"}'
        with pytest.raises(ValueError, match="INTEGRITY_INVALID"):
            await reader.read(cut, descriptor)
        assert len(calls) == 1


async def test_formal_large_assistant_source_reads_indexed_excerpt_without_full_original(
    definition_runtime,
):
    service, scope, principal, sessions = definition_runtime
    collection = str(uuid4())
    objects = await seed(sessions, principal.user_id, collection, 1, indexed_answers=True)
    async with service.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        catalog = PostgresContextSources(work.db_session, signing_secret="isolated-context-cursor")
        cut = await catalog.freeze(scope, collection)
        descriptor = (await catalog.page(cut)).sources[1]
    reads = []

    class Reader:
        async def get_bounded_bytes(self, key, limit):
            reads.append((key, limit))
            value = objects[key]
            return BoundedObjectBytes(data=value[:limit], truncated=len(value) > limit)

    async def authority(_cut, _descriptor):
        return "current-source-epoch"

    async with sessions() as db:
        await configure_session_authorization(db, AuthorizationContext.system("execution-kernel"))
        source = await ContextSourceReader(db, Reader(), authorizer=authority).read(cut, descriptor)
    assert source.messages[0].content.startswith("原始精确事实")
    assert "remaining content requires read_source" in source.messages[0].content
    assert len(source.messages[0].content.encode()) < 9000
    assert len(reads) == 2  # bounded formal index + one bounded original chunk
    assert all(limit == 65536 for _key, limit in reads)


async def test_original_pages_bind_cursor_and_recheck_authority_without_loading_whole_result(
    definition_runtime,
):
    from app.application.execution.context_compiler import compile_context
    from app.application.execution.context_read_source import ContextReadSource
    from app.domain.execution.context_models import ContextBoundary, ContextPolicy
    from app.infrastructure.execution.context_source_descriptors import ContextSourceDescriptors
    from tests.app.application.execution.test_context_compiler import PIN, PROFILE

    service, scope, principal, sessions = definition_runtime
    collection = str(uuid4())
    objects = await seed(sessions, principal.user_id, collection, 1, indexed_answers=True)
    async with service.uow_factory(
        AuthorizationContext.for_principal(principal, scope=scope)
    ) as work:
        catalog = PostgresContextSources(work.db_session, signing_secret="isolated-cursor")
        cut = await catalog.freeze(scope, collection)
        descriptors = (await catalog.page(cut)).sources
    reads, live = [], [True]

    class Storage:
        async def get_bounded_bytes(self, key, limit):
            reads.append((key, limit))
            body = objects[key]
            return BoundedObjectBytes(data=body[:limit], truncated=len(body) > limit)

    async def authorize(_cut, _descriptor):
        if not live[0]:
            raise ValueError("CONTEXT_SOURCE_AUTHORIZATION_REVOKED")
        return "epoch-1"

    class Validator:
        async def validate(self, boundary, closure):
            assert len(closure) == 2
            await authorize(boundary, closure[0])

    async with sessions() as db:
        await configure_session_authorization(db, AuthorizationContext.system("execution-kernel"))
        reader = ContextSourceReader(db, Storage(), authorizer=authorize)
        sources = tuple([await reader.read(cut, item) for item in descriptors])
        manifest = compile_context(
            pin=PIN,
            boundary=ContextBoundary(
                **cut.model_dump(exclude={"format_version"}), current_input_digest="e" * 64
            ),
            policy=ContextPolicy(),
            candidates=(PROFILE,),
            sources=sources,
        ).manifest
        assert manifest is not None
        service = ContextReadSource(
            reader=reader,
            descriptors=ContextSourceDescriptors(db),
            validator=Validator(),
            signing_secret="isolated-original-cursor",
        )
        first = await service.read(manifest, source_id=sources[1].ref.source_id, limit=8192)
        assert first.text == "原始精确事实" * (len(first.text) // 6)
        assert first.next_cursor
        second = await service.read(
            manifest, source_id=sources[1].ref.source_id, cursor=first.next_cursor
        )
        assert second.offset == len(first.text.encode())
        before = len(reads)
        with pytest.raises(ValueError, match="CURSOR_INVALID"):
            await service.read(
                manifest, source_id=sources[0].ref.source_id, cursor=first.next_cursor
            )
        with pytest.raises(ValueError, match="UNAVAILABLE"):
            await service.read(manifest, source_id=f"formal:{uuid4()}")
        live[0] = False
        with pytest.raises(ValueError, match="REVOKED"):
            await service.read(
                manifest, source_id=sources[1].ref.source_id, cursor=first.next_cursor
            )
        assert len(reads) == before
        assert all(limit <= 262144 for _, limit in reads)
        assert all(
            "/context_sources/" in key or key in {d.object_ref for d in descriptors}
            for key, _ in reads
        )
