# ruff: noqa: F401,F811 -- isolated database fixture dependencies
"""Copied memory evidence must retain current authority through the permit UoW."""

import asyncio
from contextvars import Context
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.execution.context_models import context_digest
from app.domain.execution.context_resources import (
    ContextKnowledgeVersion,
    ContextMemoryEvidence,
    ContextMemoryVersion,
    ContextResourceEvidence,
)
from app.domain.models.authorization import AuthorizationContext
from app.domain.models.knowledge_citation import KnowledgeCitation
from app.domain.models.memory_entry import MemoryEntry
from app.infrastructure.execution.context_resource_authority import validate_resource_evidence
from tests.app.alembic.test_execution_view_migration import isolated_database
from tests.app.application.services.test_execution_usage_postgres import fresh_f07_database
from tests.app.infrastructure.repositories.test_evaluation_budget_binding import (
    budget_binding_fixture,
)
from tests.app.infrastructure.repositories.test_evaluation_configuration_repository import (
    configurations,
)
from tests.app.infrastructure.repositories.test_evaluation_dataset_repository import datasets

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("fresh_f07_database")]


@pytest.fixture
async def memory_evidence(budget_binding_fixture):
    _, scope, _, _, _, _, uow = budget_binding_fixture
    auth = AuthorizationContext.system("execution-kernel")
    entry = MemoryEntry(id="context-memory", owner_user_id=scope.user_id, content="Do not renew")
    async with uow(auth) as work:
        await work.memory_entry.save(entry)
        await work.commit()
    evidence = ContextResourceEvidence(
        memory=ContextMemoryEvidence(
            query_digest=context_digest("accepted query"),
            entries=(ContextMemoryVersion.from_entry(entry),),
        )
    )
    return SimpleNamespace(
        uow=uow,
        auth=auth,
        scope=scope,
        entry=entry,
        evidence=evidence,
        payload={"message": "accepted query", "session_id": "session-1"},
    )


async def test_memory_permit_locks_revision_until_transaction_ends(memory_evidence):
    value = memory_evidence
    started, finished = asyncio.Event(), asyncio.Event()
    second = create_async_engine(
        value.uow().session_factory.kw["bind"].url, pool_size=1, max_overflow=0
    )

    async def edit():
        work = value.uow(value.auth)
        work.session_factory = async_sessionmaker(second, expire_on_commit=False)
        async with work:
            started.set()
            await work.db_session.execute(
                text("UPDATE memory_entries SET content='renew' WHERE id=:id"),
                {"id": value.entry.id},
            )
            await work.commit()
            finished.set()

    task = None
    try:
        async with value.uow(value.auth) as work:
            await validate_resource_evidence(work, value.scope, value.payload, value.evidence)
            task = asyncio.create_task(edit(), context=Context())
            await asyncio.wait_for(started.wait(), 5)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(finished.wait(), 0.1)
            await work.commit()
        await asyncio.wait_for(task, 5)
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await second.dispose()
    async with value.uow(value.auth) as work:
        with pytest.raises(ValueError, match="CONTEXT_RESOURCE_VERSION_CHANGED"):
            await validate_resource_evidence(work, value.scope, value.payload, value.evidence)


@pytest.mark.parametrize("change", ["deleted", "foreign_owner", "session", "embedding", "query"])
async def test_memory_copied_result_cannot_outlive_its_authority(memory_evidence, change):
    value = memory_evidence
    async with value.uow(value.auth) as work:
        if change == "deleted":
            await work.db_session.execute(
                text("DELETE FROM memory_entries WHERE id=:id"), {"id": value.entry.id}
            )
        elif change == "foreign_owner":
            await work.db_session.execute(
                text("UPDATE memory_entries SET owner_user_id=NULL WHERE id=:id"),
                {"id": value.entry.id},
            )
        elif change == "session":
            await work.db_session.execute(
                text("UPDATE memory_entries SET scope='session' WHERE id=:id"),
                {"id": value.entry.id},
            )
        elif change == "embedding":
            await work.db_session.execute(
                text("UPDATE memory_entries SET embedding_space_digest=:digest WHERE id=:id"),
                {"id": value.entry.id, "digest": "b" * 64},
            )
        else:
            value.payload["message"] = "later query"
        await work.commit()
    async with value.uow(value.auth) as work:
        with pytest.raises(ValueError, match="CONTEXT_RESOURCE_"):
            await validate_resource_evidence(work, value.scope, value.payload, value.evidence)


@pytest.fixture
async def knowledge_evidence(budget_binding_fixture):
    _, scope, _, _, _, _, uow = budget_binding_fixture
    return await seed_knowledge_evidence(uow, scope)


async def seed_knowledge_evidence(uow, scope):
    auth = AuthorizationContext.system("execution-kernel")
    async with uow(auth) as work:
        statements = [
            "INSERT INTO knowledge_bases(id,name,status,owner_user_id) VALUES ('ctx-kb','context','ready',:owner)",
            "INSERT INTO knowledge_base_versions(id,knowledge_base_id,state,published_at) VALUES ('ctx-v1','ctx-kb','ready',now()),('ctx-v2','ctx-kb','ready',now())",
            "UPDATE knowledge_bases SET active_version_id='ctx-v2' WHERE id='ctx-kb'",
            "INSERT INTO knowledge_documents(id,kb_id,title,source_type,source_ref,mime,status) VALUES ('ctx-doc','ctx-kb','original','upload','','','ready')",
            "INSERT INTO knowledge_document_revisions(id,document_id,source_ref,source_digest,parsed_blocks,state) VALUES ('ctx-rev','ctx-doc','',:digest,'[]'::jsonb,'indexed')",
            "INSERT INTO knowledge_base_version_documents(version_id,knowledge_base_id,document_id,document_revision_id,ordinal,state) VALUES ('ctx-v1','ctx-kb','ctx-doc','ctx-rev',0,'indexed')",
            "INSERT INTO knowledge_chunks(id,kb_id,doc_id,version_id,level,content,page_no,ordinal) VALUES ('ctx-chunk','ctx-kb','ctx-doc','ctx-v1','child','Do not renew ORD83',1,0)",
        ]
        for sql in statements:
            await work.db_session.execute(text(sql), {"owner": scope.user_id, "digest": "b" * 64})
        await work.commit()
    citation = KnowledgeCitation(
        knowledge_base_id="ctx-kb",
        version_id="ctx-v1",
        doc_id="ctx-doc",
        document_revision_id="ctx-rev",
        chunk_id="ctx-chunk",
        page_no=1,
    )
    evidence = ContextResourceEvidence(
        knowledge=(
            ContextKnowledgeVersion(
                knowledge_base_id="ctx-kb", version_id="ctx-v1", citations=(citation,)
            ),
        )
    )
    return SimpleNamespace(
        uow=uow,
        auth=auth,
        scope=scope,
        evidence=evidence,
        payload={
            "resource_bindings": [
                {
                    "binding_id": "ctx-binding",
                    "resource_kind": "knowledge_base",
                    "resource_id": "ctx-kb",
                    "version_id": "ctx-v1",
                }
            ]
        },
    )


async def test_knowledge_uses_exact_historical_version_and_current_owner(knowledge_evidence):
    value = knowledge_evidence
    async with value.uow(value.auth) as work:
        await validate_resource_evidence(work, value.scope, value.payload, value.evidence)
        await work.commit()
    async with value.uow(value.auth) as work:
        await work.db_session.execute(
            text("UPDATE knowledge_bases SET deleted_at=now() WHERE id='ctx-kb'")
        )
        await work.commit()
    async with value.uow(value.auth) as work:
        with pytest.raises(ValueError, match="CONTEXT_RESOURCE_AUTHORIZATION_REVOKED"):
            await validate_resource_evidence(work, value.scope, value.payload, value.evidence)


@pytest.mark.parametrize("change", ["version", "revision", "page", "embedding"])
async def test_knowledge_cannot_substitute_active_version_or_changed_provenance(
    knowledge_evidence, change
):
    value = knowledge_evidence
    body = value.evidence.model_dump(mode="json")
    expected = body["knowledge"][0]
    if change == "version":
        expected["version_id"] = "ctx-v2"
    elif change == "revision":
        expected["citations"][0]["document_revision_id"] = "different-revision"
    elif change == "page":
        expected["citations"][0]["page_no"] = 2
    else:
        expected["embedding_space_digest"] = "a" * 64
    async with value.uow(value.auth) as work:
        with pytest.raises(ValueError, match="CONTEXT_RESOURCE_"):
            await validate_resource_evidence(work, value.scope, value.payload, body)
