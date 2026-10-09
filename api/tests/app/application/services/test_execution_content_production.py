"""Actual worker→tool catalog→fixed source→snapshot→command→projection path."""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

from tests.app.application.services.test_artifact_provenance_postgres import Objects, production_run
from tests.app.execution_test_support import execution_admin_session

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("postgres_integration")]


async def test_real_worker_catalog_captures_full_inputs_outputs_and_owned_fixed_citation():
    from app.application.execution.activities.tool_call import ToolCallActivityHandler
    from app.application.execution.activity_inputs import ActivityObjectStore
    from app.application.execution.activity_registry import create_activity_registry
    from app.application.execution.activity_worker import ActivityWorker
    from app.application.execution.agent_tool_catalog import AgentToolCatalog
    from app.application.execution.run_service import RunService
    from app.application.services.execution_content_service import ExecutionContentService
    from app.application.services.execution_view_service import ExecutionViewService
    from app.domain.execution.activity import ActivityClaim
    from app.domain.models.authorization import AuthorizationContext
    from app.domain.runtime_policy import KnowledgeRetrievalRunPolicy
    from app.domain.services.tools.knowledge_base_tools import KnowledgeBaseTool
    from app.infrastructure.execution.models import ExecutionActivityTaskORM
    from app.infrastructure.execution.postgres_activity_store import PostgresActivityStore
    from app.infrastructure.execution.postgres_execution_content import ExecutionContentWriter
    from app.infrastructure.execution.postgres_execution_view import PostgresExecutionView
    from app.infrastructure.execution.postgres_formal_projector import PostgresFormalProjector
    from app.infrastructure.execution.postgres_run_context_source import PostgresRunContextSource
    from app.infrastructure.repositories.db_execution_content_repository import (
        DBExecutionContentRepository,
    )
    from app.infrastructure.repositories.db_knowledge_base_repository import (
        DBKnowledgeBaseRepository,
    )

    _session_id, producer, command, handler = await production_run()
    kb, version, doc, revision, chunk = [uuid4().hex for _ in range(5)]
    source = "旧版本正文🙂汉字" * 100
    async with execution_admin_session() as db:
        await db.execute(
            text("INSERT INTO knowledge_bases(id,owner_user_id) VALUES (:id,:owner)"),
            {"id": kb, "owner": producer.scope.user_id},
        )
        await db.execute(
            text(
                "INSERT INTO knowledge_base_versions(id,knowledge_base_id,state,published_at) VALUES (:id,:kb,'ready',CURRENT_TIMESTAMP)"
            ),
            {"id": version, "kb": kb},
        )
        await db.execute(
            text(
                "INSERT INTO knowledge_documents(id,kb_id,title,status) VALUES (:id,:kb,'source','ready')"
            ),
            {"id": doc, "kb": kb},
        )
        await db.execute(
            text(
                "INSERT INTO knowledge_document_revisions(id,document_id,source_digest,state) VALUES (:id,:doc,:digest,'indexed')"
            ),
            {"id": revision, "doc": doc, "digest": "d" * 64},
        )
        await db.execute(
            text(
                "INSERT INTO knowledge_base_version_documents(version_id,knowledge_base_id,document_id,document_revision_id,ordinal,state) VALUES (:version,:kb,:doc,:revision,0,'indexed')"
            ),
            {"version": version, "kb": kb, "doc": doc, "revision": revision},
        )
        await db.execute(
            text(
                "INSERT INTO knowledge_chunks(id,kb_id,doc_id,version_id,level,content,page_no,ordinal) VALUES (:id,:kb,:doc,:version,'parent',:content,1,0)"
            ),
            {"id": chunk, "kb": kb, "doc": doc, "version": version, "content": source},
        )
        await db.commit()

    @asynccontextmanager
    async def uow():
        async with execution_admin_session() as db:
            yield SimpleNamespace(
                knowledge_base=DBKnowledgeBaseRepository(db),
                execution_content=DBExecutionContentRepository(db),
                commit=db.commit,
            )

    # Only assembly of unrelated model/tool integrations is supplied by test;
    # actual catalog invoke, KnowledgeBaseTool and exact SQL source reader run.
    tool = KnowledgeBaseTool(
        uow,
        kb,
        version,
        policy=KnowledgeRetrievalRunPolicy(
            vector_enabled=False, graph_enabled=False, retrieval={}, rerank={}
        ),
        owner_scope=producer.scope,
    )

    class Catalog(AgentToolCatalog):
        async def _build(self, payload, context):
            return SimpleNamespace(packs=[tool], clients=(), fingerprint="fixed")

    catalog = object.__new__(Catalog)
    objects = ActivityObjectStore(Objects())
    input_ref, digest = await objects.put_input(
        producer.run_id, {"message": "read source", "attachments": []}
    )
    activity = uuid4()
    tool_call = {"call_id": "call-fixed", "name": "get_document", "arguments": {"doc_id": doc}}
    await command(
        "RequestActivity",
        {
            "activity_id": str(activity),
            "activity_type": "tool.call",
            "timeout_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
            "input_ref": input_ref,
            "input_digest": digest,
            "input_payload": {"tool_call": tool_call},
        },
        2,
    )
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "UPDATE execution_activity_tasks SET status='claimed',claim_generation=1,claimed_by='f06-worker',claim_deadline=CURRENT_TIMESTAMP+INTERVAL '5 minutes' WHERE activity_id=:id"
            ),
            {"id": activity},
        )
        row = await db.get(ExecutionActivityTaskORM, activity)
        claim = ActivityClaim(
            request=PostgresActivityStore._request(row),
            claim_generation=1,
            owner_user_id=producer.scope.user_id,
            team_id=None,
            recovered_after_call_started=False,
        )
        await db.commit()
    auth = AuthorizationContext.system("f06")
    await PostgresFormalProjector(
        session_factory=execution_admin_session, authorization=auth
    ).run_once(producer.scope, limit=1000)
    writer = ExecutionContentWriter(
        session_factory=execution_admin_session, authorization=auth, objects=objects
    )
    worker = ActivityWorker(
        store=PostgresActivityStore(session_factory=execution_admin_session, authorization=auth),
        run_contexts=PostgresRunContextSource(
            session_factory=execution_admin_session, authorization=auth
        ),
        run_service=RunService(orchestrator=handler),
        registry=create_activity_registry(ToolCallActivityHandler(objects=objects, tools=catalog)),
        worker_id="f06-worker",
        content_writer=writer,
    )
    outcome = await worker._execute_claim(claim, now=datetime.now(UTC))
    async with execution_admin_session() as db:
        failure = await db.scalar(
            text("SELECT failure_code FROM execution_activity_tasks WHERE activity_id=:id"),
            {"id": activity},
        )
    assert outcome == "succeeded", failure
    await PostgresFormalProjector(
        session_factory=execution_admin_session, authorization=auth
    ).run_once(producer.scope, limit=1000)
    views = ExecutionViewService(
        PostgresExecutionView(session_factory=execution_admin_session, authorization=auth),
        cursor_secret=b"f06-producer-view-secret",
    )
    view = await views.get_view(producer.scope, producer.run_id)
    step = next(s for s in view.steps if s.activity_id == activity)
    assert step.input_ref
    assert step.output_ref
    assert len(step.citation_refs) == 1
    citation = step.citation_refs[0]
    assert (
        citation.knowledge_base_id,
        citation.version_id,
        citation.document_revision_id,
        citation.doc_id,
        citation.chunk_id,
    ) == (kb, version, revision, doc, chunk)
    reader = ExecutionContentService(uow, views, None, cursor_secret=b"f06-source-body-secret")
    input_page = await reader.read_step_content(
        producer.scope, producer.run_id, step.step_id, view.at, content_kind="input"
    )
    assert json.loads(input_page.content)["request"]["tool_call"] == tool_call
    source_page = await reader.read_source(producer.scope, citation, limit_bytes=64)
    parts = [source_page.content]
    while source_page.next_cursor:
        source_page = await reader.read_source(
            producer.scope, citation, cursor=source_page.next_cursor, limit_bytes=64
        )
        parts.append(source_page.content)
    assert "".join(parts) == source
    # A missing chunk narrows to the cited page inside the same immutable revision.
    page_ref = citation.model_dump(mode="json")
    page_ref["chunk_id"] = None
    whole = await reader.read_source(producer.scope, page_ref)
    assert whole.content == source
    # New active version cannot change old citation resolution.
    newer = uuid4().hex
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "INSERT INTO knowledge_base_versions(id,knowledge_base_id,state,published_at) VALUES (:id,:kb,'ready',CURRENT_TIMESTAMP)"
            ),
            {"id": newer, "kb": kb},
        )
        await db.execute(
            text("UPDATE knowledge_bases SET active_version_id=:version WHERE id=:id"),
            {"id": kb, "version": newer},
        )
        await db.commit()
    assert (await reader.read_source(producer.scope, citation)).content == source
    from app.application.ports.execution_view import ViewCursorInvalid, ViewNotFound

    forged = citation.model_dump(mode="json")
    forged["chunk_id"] = "missing-chunk"
    with pytest.raises(ViewNotFound):
        await reader.read_source(producer.scope, forged)
    cursor = (await reader.read_source(producer.scope, citation, limit_bytes=4)).next_cursor
    with pytest.raises(ViewCursorInvalid):
        await reader.read_source(producer.scope, page_ref, cursor=cursor)
    from app.domain.models.resource_pin import ResourceIdentity, ResourceUnavailable
    from app.infrastructure.repositories.db_resource_pin_repository import DBResourcePinRepository

    async with execution_admin_session() as db:
        digest = await db.scalar(
            text("SELECT content_digest FROM execution_public_content WHERE content_id=:id"),
            {"id": step.output_ref.content_id},
        )
        pin = ResourceIdentity(
            resource_kind="execution_content",
            resource_id=step.output_ref.content_id,
            resource_version=digest,
        )
        await DBResourcePinRepository(db).acquire(
            producer.scope, "run", str(producer.run_id), [pin]
        )
        await db.commit()
    # A physically missing original locator is distinct from loss of its fixed source.
    # These rows are owned by this invocation; preserve another same-page parent.
    async with execution_admin_session() as db:
        await db.execute(
            text(
                "INSERT INTO knowledge_chunks(id,kb_id,doc_id,version_id,level,content,page_no,ordinal) VALUES (:id,:kb,:doc,:version,'parent','remaining same revision',1,1)"
            ),
            {"id": uuid4().hex, "kb": kb, "doc": doc, "version": version},
        )
        await db.execute(text("DELETE FROM knowledge_chunks WHERE id=:id"), {"id": chunk})
        await db.commit()
    missing = await reader.read_source(producer.scope, {"citation_id": citation.citation_id})
    assert missing.availability == "unavailable"
    assert missing.reason == "source_locator_unavailable"
    assert missing.content is None
    assert missing.next_cursor is None
    fallback = await reader.read_source(producer.scope, page_ref)
    assert fallback.content == "remaining same revision"
    assert fallback.source_title == "source"
    # Source revocation invalidates direct source and derived snapshot body access.
    from tests.app.application.services.test_artifact_provenance_postgres import seed

    other, _ = await seed()
    async with execution_admin_session() as db:
        await db.execute(
            text("UPDATE knowledge_bases SET owner_user_id=:owner WHERE id=:id"),
            {"id": kb, "owner": other},
        )
        await db.commit()
    with pytest.raises(ViewNotFound):
        await reader.read_source(producer.scope, citation)
    with pytest.raises(ViewNotFound):
        await reader.read_step_content(producer.scope, producer.run_id, step.step_id, view.at)

    async with execution_admin_session() as db:
        repo = DBResourcePinRepository(db)
        assert not (await repo.validate(producer.scope, "run", str(producer.run_id), [pin]))[
            0
        ].available
        with pytest.raises(ResourceUnavailable):
            await repo.acquire(producer.scope, "run", str(producer.run_id), [pin])
