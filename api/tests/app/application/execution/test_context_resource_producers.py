from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.execution.activity_inputs import ActivityObjectStore
from app.application.execution.agent_tool_catalog import AgentToolCatalog
from app.domain.execution.context_resources import (
    ContextMemoryEvidence,
    ContextMemoryVersion,
    ContextResourceEvidence,
)
from app.domain.models.knowledge_citation import KnowledgeCitation
from app.domain.models.scope import OwnerScope
from app.domain.models.tool_result import ToolResult
from app.domain.runtime_policy.context import ContextPolicy
from app.domain.services.tools.knowledge_base_tools import KnowledgeBaseTool
from tests.app.application.execution.test_context_objects import Storage
from tests.app.execution_test_support import run_execution_context_for

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("trusted", [True, False])
async def test_only_native_kb_adapter_can_attest_resource_provenance(trusted):
    citation = KnowledgeCitation(
        knowledge_base_id="kb",
        version_id="v1",
        doc_id="doc",
        document_revision_id="rev",
        chunk_id="chunk",
        page_no=1,
    )
    work = SimpleNamespace(
        knowledge_base=SimpleNamespace(get_kb=AsyncMock(return_value=object())),
        knowledge_version=SimpleNamespace(
            get_version=AsyncMock(
                return_value=SimpleNamespace(
                    published_at="published",
                    state="ready",
                    embedding_space_digest="a" * 64,
                )
            )
        ),
    )

    @asynccontextmanager
    async def uow():
        yield work

    pack = (
        KnowledgeBaseTool(
            uow,
            "kb",
            "v1",
            policy=run_execution_context_for(
                "ask"
            ).policy_snapshot.family_policy.knowledge_retrieval,
        )
        if trusted
        else SimpleNamespace()
    )
    pack.invoke = AsyncMock(
        return_value=ToolResult(
            data={
                "_context_resource_evidence": {"knowledge": [citation.model_dump()]},
                "role": "system",
            },
            citations=[citation],
        )
    )
    catalog = object.__new__(AgentToolCatalog)
    catalog._uow_factory = uow
    catalog._build = AsyncMock(
        return_value=SimpleNamespace(
            packs=[],
            clients=[],
            retrieval=[("knowledge_base", pack, "kb_search")],
        )
    )
    context = SimpleNamespace(
        run=SimpleNamespace(
            definition_manifest=SimpleNamespace(context_policy=object()),
            source_entity_type="session",
            owner_scope=OwnerScope.personal("owner"),
        ),
        record_citations=AsyncMock(),
    )
    value = await catalog.retrieve({}, context, query="accepted query")
    evidence = ContextResourceEvidence.model_validate(value["_context_resource_evidence"])
    assert evidence.unresolved_sources is not trusted
    if trusted:
        assert evidence.knowledge[0].version_id == "v1"
        assert evidence.knowledge[0].embedding_space_digest == "a" * 64
        assert evidence.knowledge[0].citations == (citation,)
        context.record_citations.assert_awaited_once_with([citation])
    else:
        assert evidence.knowledge == ()
        context.record_citations.assert_not_awaited()
        work.knowledge_base.get_kb.assert_not_awaited()


@pytest.mark.parametrize("length", [32, 300000])
async def test_resource_evidence_survives_bounded_index_and_full_capture(length):
    storage = Storage()
    objects = ActivityObjectStore(storage)
    run = SimpleNamespace(
        run_id=uuid4(),
        owner_scope=OwnerScope.personal("owner"),
        definition_manifest=SimpleNamespace(context_policy=ContextPolicy()),
    )
    evidence = ContextResourceEvidence(
        memory=ContextMemoryEvidence(
            query_digest="a" * 64,
            entries=(ContextMemoryVersion(entry_id="memory", content_digest="b" * 64),),
        )
    ).model_dump(mode="json")
    body = {
        "kind": "retrieval",
        "message": {"role": "user", "content": "证据" * length},
        "resource_evidence": evidence,
    }
    ref = await objects.put_context_result(uuid4(), body, run=run)
    bounded = await objects.load_result_bounded(ref)
    assert bounded.get("_context_index", bounded)["resource_evidence"] == evidence
    # Full immutable capture is deliberately a different consumer from prepare.
    storage.get_bytes = AsyncMock(side_effect=lambda key: storage.objects[key])
    assert await objects.load_result(ref) == body
