"""Current resource checks are held by the same transaction as a send permit."""

from sqlalchemy import select

from app.domain.execution.context_models import context_digest
from app.domain.execution.context_resources import ContextResourceEvidence, memory_content_digest
from app.domain.models.resource_pin import ResourceIdentity, ResourceUnavailable
from app.infrastructure.execution.original_evidence import retain_read
from app.infrastructure.models.memory_entry import MemoryEntryORM
from app.infrastructure.repositories.db_resource_pin_repository import DBResourcePinRepository


async def validate_input_resources(work, scope, payload):
    from app.infrastructure.execution.context_file_authority import validate_context_files
    from app.infrastructure.execution.context_skill_authority import validate_context_skill

    await validate_context_skill(work.db_session, scope, payload)
    await validate_context_files(work.db_session, scope, payload)
    bindings = payload.get("resource_bindings", [])
    if not isinstance(bindings, list) or len(bindings) > 100:
        raise ValueError("CONTEXT_RESOURCE_IDENTITY_CONFLICT")
    identities = []
    for binding in bindings:
        if not isinstance(binding, dict) or binding.get("resource_kind") != "knowledge_base":
            raise ValueError("CONTEXT_RESOURCE_DEPENDENCIES_UNAVAILABLE")
        identities.append(
            ResourceIdentity(
                resource_kind="knowledge_base",
                resource_id=binding.get("resource_id"),
                resource_version=binding.get("version_id"),
            )
        )
    for identity in sorted(identities, key=lambda item: (item.resource_id, item.resource_version)):
        try:
            await DBResourcePinRepository(work.db_session).resolve(scope, identity, lock=True)
        except ResourceUnavailable as error:
            raise ValueError("CONTEXT_RESOURCE_AUTHORIZATION_REVOKED") from error


async def validate_resource_evidence(work, scope, payload, evidence):
    evidence = ContextResourceEvidence.model_validate(evidence)
    if evidence.unresolved_sources:
        raise ValueError("CONTEXT_RESOURCE_DEPENDENCIES_UNAVAILABLE")
    bindings = {
        (item.get("resource_id"), item.get("version_id"))
        for item in payload.get("resource_bindings", [])
        if isinstance(item, dict) and item.get("resource_kind") == "knowledge_base"
    }
    for expected in sorted(
        evidence.knowledge, key=lambda item: (item.knowledge_base_id, item.version_id)
    ):
        if (expected.knowledge_base_id, expected.version_id) not in bindings:
            raise ValueError("CONTEXT_RESOURCE_IDENTITY_CONFLICT")
        try:
            await DBResourcePinRepository(work.db_session).resolve(
                scope,
                ResourceIdentity(
                    resource_kind="knowledge_base",
                    resource_id=expected.knowledge_base_id,
                    resource_version=expected.version_id,
                ),
                lock=True,
            )
        except ResourceUnavailable as error:
            raise ValueError("CONTEXT_RESOURCE_AUTHORIZATION_REVOKED") from error
        version = await work.knowledge_version.get_version(
            expected.version_id, knowledge_base_id=expected.knowledge_base_id
        )
        if version is None or version.embedding_space_digest != expected.embedding_space_digest:
            raise ValueError("CONTEXT_RESOURCE_VERSION_CHANGED")
        for citation in expected.citations:
            if (citation.knowledge_base_id, citation.version_id) != (
                expected.knowledge_base_id,
                expected.version_id,
            ):
                raise ValueError("CONTEXT_RESOURCE_IDENTITY_CONFLICT")
            chunks = await work.knowledge_base.get_chunks_by_ids_for_version(
                expected.knowledge_base_id, expected.version_id, [citation.chunk_id]
            )
            if not any(
                item.chunk.id == citation.chunk_id
                and item.chunk.doc_id == citation.doc_id
                and item.chunk.kb_id == citation.knowledge_base_id
                and item.chunk.page_no == citation.page_no
                and item.document_revision_id == citation.document_revision_id
                for item in chunks
            ):
                raise ValueError("CONTEXT_RESOURCE_VERSION_CHANGED")
    memory = evidence.memory
    if memory is None:
        return
    if memory.query_digest != context_digest(payload.get("message")):
        raise ValueError("CONTEXT_RESOURCE_IDENTITY_CONFLICT")
    identities = [entry.entry_id for entry in memory.entries]
    if len(set(identities)) != len(identities):
        raise ValueError("CONTEXT_RESOURCE_IDENTITY_CONFLICT")
    for expected in sorted(memory.entries, key=lambda entry: entry.entry_id):
        query = select(MemoryEntryORM).where(MemoryEntryORM.id == expected.entry_id)
        if scope.team_id:
            query = query.where(MemoryEntryORM.team_id == scope.team_id)
        else:
            query = query.where(
                MemoryEntryORM.team_id.is_(None), MemoryEntryORM.owner_user_id == scope.user_id
            )
        result = await work.db_session.execute(query.with_for_update(read=True))
        try:
            row = result.scalar_one_or_none()
            retain_read(
                work.db_session,
                "resource-source",
                "context.memory",
                {"scope": scope, "id": expected.entry_id},
                row,
                source_result=result,
            )
        finally:
            result.close()
            synchronous = getattr(work.db_session, "sync_session", work.db_session)
            forget = getattr(synchronous, "forget_result", None)
            if callable(forget):
                forget(result)
        if row is None or (
            row.scope != "global"
            and (row.scope != "session" or row.session_id != payload.get("session_id"))
        ):
            raise ValueError("CONTEXT_RESOURCE_AUTHORIZATION_REVOKED")
        if memory_content_digest(row.to_domain()) != expected.content_digest:
            raise ValueError("CONTEXT_RESOURCE_VERSION_CHANGED")
        if row.embedding_space_digest != expected.embedding_space_digest:
            raise ValueError("CONTEXT_RESOURCE_VERSION_CHANGED")
