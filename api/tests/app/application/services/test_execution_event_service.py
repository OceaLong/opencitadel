from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.application.execution.public_projection import PublicEventPage, PublicExecutionEvent
from app.application.ports.execution_view import ViewCursorInvalid, ViewRevisionExpired
from app.domain.models.scope import OwnerScope


@pytest.mark.asyncio
async def test_event_cursor_binds_run_scope_direction_and_generation_but_survives_append():
    from app.application.services.execution_event_service import ExecutionEventService

    class Port:
        generation = "g1"

        def __init__(self):
            self.positions = ["one", "two"]

        async def read(self, scope, run, *, after, before, latest, limit, generation):
            if generation is not None and generation != self.generation:
                raise ViewRevisionExpired()
            positions = (
                self.positions[self.positions.index(after) + 1 :] if after else self.positions
            )
            if before:
                positions = positions[: positions.index(before)]
            positions = positions[-limit:] if latest or before else positions[:limit]
            events = tuple(
                PublicExecutionEvent(
                    cursor=p,
                    event_id=uuid4(),
                    run_id=run,
                    event_type="message",
                    stream_id="run",
                    stream_version=1,
                    payload={"event_id": p},
                    occurred_at=datetime.now(UTC),
                )
                for p in positions
            )
            return self.generation, PublicEventPage(
                events=events,
                next_cursor=positions[-1] if positions else None,
                prev_cursor=positions[0] if positions else None,
                has_earlier=False,
            )

    port = Port()
    service = ExecutionEventService(port, cursor_secret=b"0123456789abcdef")
    scope, run = OwnerScope.personal("u1"), uuid4()
    page = await service.list_events(scope, run, limit=1)
    cursor = page.events[0].cursor
    assert cursor != "one"
    assert page.events[0].payload["event_id"] == cursor
    port.positions.append("three")
    assert len((await service.list_events(scope, run, after=cursor)).events) == 2
    for other_scope, other_run, query in [
        (scope, uuid4(), {"after": cursor}),
        (OwnerScope.personal("u2"), run, {"after": cursor}),
        (scope, run, {"before": cursor}),
    ]:
        with pytest.raises(ViewCursorInvalid):
            await service.list_events(other_scope, other_run, **query)
    port.generation = "g2"
    with pytest.raises(ViewRevisionExpired):
        await service.list_events(scope, run, after=cursor)


@pytest.mark.asyncio
async def test_binary_source_download_uses_canonical_fixed_identity_and_checks_authority():
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from app.application.ports.execution_view import ViewNotFound
    from app.application.services.execution_content_service import ExecutionContentService

    canonical = {
        "availability": "available",
        "citation_id": "c",
        "resource_kind": "file",
        "file_id": "f",
        "content_digest": "digest",
        "object_identity": "fixed",
    }

    class Content:
        async def get_citation(self, scope, citation_id):
            return canonical if citation_id == "c" else None

    class Files:
        async def read_fixed(self, file_id, digest, identity, scope):
            assert (file_id, digest, identity) == ("f", "digest", "fixed")
            return b"\x00\xffbinary"

    class FileRepository:
        async def get_by_id(self, file_id, scope):
            return SimpleNamespace(
                content_digest="digest", object_identity="fixed", content_available=True
            )

    @asynccontextmanager
    async def uow():
        yield SimpleNamespace(
            execution_content=Content(), file=FileRepository(), knowledge_base=None
        )

    service = ExecutionContentService(
        uow, None, None, cursor_secret=b"0123456789abcdef", files=Files()
    )
    assert await service.download_file_source(OwnerScope.personal("u"), "c") == b"\x00\xffbinary"
    with pytest.raises(ViewNotFound):
        await service.download_file_source(OwnerScope.personal("u"), "other")


@pytest.mark.asyncio
async def test_source_id_defaults_to_its_canonical_cited_chunk():
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from app.application.services.execution_content_service import ExecutionContentService

    class Content:
        async def get_citation(self, scope, citation_id):
            return {
                "citation_id": "c",
                "availability": "available",
                "resource_kind": "knowledge_base",
                "knowledge_base_id": "kb",
                "version_id": "v1",
                "doc_id": "doc",
                "document_revision_id": "rev",
                "chunk_id": "chunk",
                "page_no": 1,
            }

    class Knowledge:
        async def get_kb(self, *args, **kwargs):
            return object()

        async def get_document_for_version(self, *args):
            return (SimpleNamespace(title="Cited document"), "rev")

        async def get_chunks_by_ids_for_version(self, *args):
            return [
                SimpleNamespace(
                    chunk=SimpleNamespace(
                        id="chunk",
                        kb_id="kb",
                        version_id="v1",
                        doc_id="doc",
                        page_no=1,
                        content="cited chunk",
                    ),
                    document_revision_id="rev",
                )
            ]

        async def read_document_page_for_version(self, *args, **kwargs):
            return SimpleNamespace(
                items=[SimpleNamespace(content="whole document")], next_cursor=None
            )

    @asynccontextmanager
    async def uow():
        yield SimpleNamespace(execution_content=Content(), knowledge_base=Knowledge())

    reader = ExecutionContentService(uow, None, None, cursor_secret=b"0123456789abcdef")
    page = await reader.read_source(OwnerScope.personal("u"), {"citation_id": "c"})
    assert page.content == "cited chunk"
    assert page.source_title == "Cited document"
