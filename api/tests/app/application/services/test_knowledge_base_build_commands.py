#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Application compatibility and post-commit dispatch contracts."""
import hashlib
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
)
from app.application.services.knowledge_base_service import KnowledgeBaseService
from app.domain.models.file import File
from app.domain.models.knowledge_base import (
    KBSourceType,
    KBStatus,
    KnowledgeBase,
    KnowledgeDocument,
)
from app.domain.models.knowledge_version import (
    KnowledgeBaseVersion,
    KnowledgeDocumentRevision,
    KnowledgeVersionDocument,
    KnowledgeVersionState,
)
from app.domain.models.resource_governance import BuildState, ResourceBuild, ResourceKind
from app.domain.models.scope import OwnerScope
from app.domain.services.knowledge_base.web_connector import WebDocument


class _Builder:
    def __init__(self, result):
        self.result = result
        self.commands = []

    async def create_candidate(self, command, *, scope):
        self.commands.append((command, scope))
        return self.result


class _ValidationUow:
    def __init__(self, kb, file=None):
        self.knowledge_base = SimpleNamespace(
            get_kb=AsyncMock(return_value=kb),
            count_ready_documents=AsyncMock(return_value={kb.id: 1}),
        )
        self.file = SimpleNamespace(get_by_id=AsyncMock(return_value=file))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _MutationSpyUow:
    def __init__(self, kb, file=None):
        self.knowledge_base = SimpleNamespace(
            get_kb=AsyncMock(return_value=kb),
            count_ready_documents=AsyncMock(return_value={kb.id: 0}),
            save_document=AsyncMock(),
            delete_document=AsyncMock(),
            clear_index_data=AsyncMock(),
            mark_documents_pending=AsyncMock(),
            save_kb=AsyncMock(),
        )
        self.file = SimpleNamespace(get_by_id=AsyncMock(return_value=file))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _ReindexUow:
    def __init__(self, kb, document, revision):
        self.knowledge_base = SimpleNamespace(
            get_kb=AsyncMock(return_value=kb),
            count_ready_documents=AsyncMock(return_value={kb.id: 1}),
            get_document=AsyncMock(return_value=document),
        )
        self.knowledge_version = SimpleNamespace(
            get_manifest=AsyncMock(
                return_value=[
                    KnowledgeVersionDocument(
                        version_id="v1",
                        document_id=document.id,
                        document_revision_id=revision.id,
                        ordinal=0,
                    )
                ]
            ),
            get_revisions=AsyncMock(return_value={revision.id: revision}),
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _candidate_result(*, created=True):
    version = KnowledgeBaseVersion(
        id="version-new",
        knowledge_base_id="kb1",
        parent_version_id="v1",
        build_id="build-new",
    )
    build = ResourceBuild(
        id="build-new",
        resource_kind=ResourceKind.KNOWLEDGE_BASE,
        resource_id="kb1",
        version_id=version.id,
        parent_version_id="v1",
        command_key="abc",
        state=BuildState.QUEUED,
        created_by="user1",
    )
    resource = KnowledgeBase(
        id="kb1",
        status=KBStatus.PENDING,
        active_version_id="v1",
        ingest_task_id=build.id,
        owner_user_id="user1",
    )
    return SimpleNamespace(
        resource=resource,
        version=version,
        build=build,
        created=created,
    )


@pytest.mark.asyncio
async def test_add_validates_file_then_returns_queued_build_compatibility_projection():
    kb = KnowledgeBase(
        id="kb1", name="KB", status=KBStatus.READY,
        active_version_id="v1", owner_user_id="user1",
    )
    file = File(
        id="file1", filename="doc.md", mime_type="text/markdown",
        owner_user_id="user1",
    )
    uow = _ValidationUow(kb, file)
    builder = _Builder(_candidate_result())
    storage = SimpleNamespace(
        download_file=AsyncMock(return_value=(BytesIO(b"immutable"), file))
    )
    dispatch = AsyncMock()
    service = KnowledgeBaseService(
        uow_factory=lambda: uow,
        file_storage=storage,
        version_builder=builder,
        build_dispatcher=dispatch,
    )

    response = await service.add_documents(
        "kb1", file_ids=["file1"], scope=OwnerScope.personal("user1")
    )

    command, _ = builder.commands[0]
    assert command.operation.value == "add"
    assert command.sources[0].source_digest
    assert response.ingest_task_id == "build-new"
    assert response.status is KBStatus.PENDING
    dispatch.assert_awaited_once_with("build-new", "kb1")


@pytest.mark.asyncio
async def test_exact_duplicate_does_not_dispatch_again_and_failure_is_recoverable():
    kb = KnowledgeBase(
        id="kb1", active_version_id=None, owner_user_id="user1"
    )
    uow = _ValidationUow(kb)
    builder = _Builder(_candidate_result(created=False))
    dispatch = AsyncMock(side_effect=RuntimeError("redis unavailable"))
    service = KnowledgeBaseService(
        uow_factory=lambda: uow,
        file_storage=object(),
        version_builder=builder,
        build_dispatcher=dispatch,
    )

    response = await service.reindex(
        "kb1", scope=OwnerScope.personal("user1")
    )
    assert response.ingest_task_id == "build-new"
    dispatch.assert_not_awaited()

    builder.result = _candidate_result(created=True)
    response = await service.reindex(
        "kb1", scope=OwnerScope.personal("user1")
    )
    assert response.ingest_task_id == "build-new"
    dispatch.assert_awaited_once()


@pytest.mark.asyncio
async def test_url_add_hashes_downloaded_immutable_content(monkeypatch):
    kb = KnowledgeBase(
        id="kb1", active_version_id=None, owner_user_id="user1"
    )
    uow = _ValidationUow(kb)
    builder = _Builder(_candidate_result())
    monkeypatch.setattr(
        "app.application.services.knowledge_base_service.validate_public_url",
        lambda value: value,
    )
    service = KnowledgeBaseService(
        uow_factory=lambda: uow,
        file_storage=object(),
        version_builder=builder,
        build_dispatcher=AsyncMock(),
        web_fetcher=AsyncMock(
            return_value=WebDocument(
                title="Downloaded",
                content="version-one-content",
            )
        ),
    )

    await service.add_documents(
        "kb1",
        urls=["https://example.com/doc"],
        source_type=KBSourceType.WEB,
        scope=OwnerScope.personal("user1"),
    )

    source = builder.commands[0][0].sources[0]
    assert source.title == "Downloaded"
    assert (
        source.source_digest
        == hashlib.sha256(b"version-one-content").hexdigest()
    )


@pytest.mark.parametrize(
    ("source_type", "fetcher_name"),
    [
        (KBSourceType.WEB, "web_fetcher"),
        (KBSourceType.CONFLUENCE, "confluence_fetcher"),
        (KBSourceType.FEISHU, "feishu_fetcher"),
    ],
)
@pytest.mark.asyncio
async def test_each_url_source_hashes_typed_connector_content(
    monkeypatch,
    source_type,
    fetcher_name,
):
    kb = KnowledgeBase(
        id="kb1",
        active_version_id=None,
        owner_user_id="user1",
    )
    uow = _ValidationUow(kb)
    builder = _Builder(_candidate_result())
    monkeypatch.setattr(
        "app.application.services.knowledge_base_service.validate_public_url",
        lambda value: value,
    )
    connector = AsyncMock(
        return_value=WebDocument(
            title=f"{source_type.value}-title",
            content=f"{source_type.value}-immutable-content",
            mime="text/markdown",
        )
    )
    service_kwargs = {
        "uow_factory": lambda: uow,
        "file_storage": object(),
        "version_builder": builder,
        "build_dispatcher": AsyncMock(),
        fetcher_name: connector,
    }
    service = KnowledgeBaseService(**service_kwargs)

    await service.add_documents(
        "kb1",
        urls=["https://example.com/doc"],
        source_type=source_type,
        scope=OwnerScope.personal("user1"),
    )

    source = builder.commands[0][0].sources[0]
    assert source.source_type is source_type
    assert source.source_identity == (
        f"{source_type.value}:https://example.com/doc"
    )
    assert source.source_digest == hashlib.sha256(
        f"{source_type.value}-immutable-content".encode()
    ).hexdigest()
    connector.assert_awaited_once_with("https://example.com/doc")


@pytest.mark.parametrize(
    "source_type",
    [
        KBSourceType.WEB,
        KBSourceType.CONFLUENCE,
        KBSourceType.FEISHU,
    ],
)
@pytest.mark.asyncio
async def test_url_connector_failure_has_zero_durable_effect(
    monkeypatch,
    source_type,
):
    kb = KnowledgeBase(
        id="kb1",
        active_version_id=None,
        owner_user_id="user1",
    )
    uow = _ValidationUow(kb)
    builder = _Builder(_candidate_result())
    monkeypatch.setattr(
        "app.application.services.knowledge_base_service.validate_public_url",
        lambda value: value,
    )
    failing = AsyncMock(side_effect=RuntimeError("blocked redirect"))
    service = KnowledgeBaseService(
        uow_factory=lambda: uow,
        file_storage=object(),
        version_builder=builder,
        build_dispatcher=AsyncMock(),
        web_fetcher=failing,
        confluence_fetcher=failing,
        feishu_fetcher=failing,
    )

    with pytest.raises(BadRequestError, match="无法下载"):
        await service.add_documents(
            "kb1",
            urls=["https://example.com/doc"],
            source_type=source_type,
            scope=OwnerScope.personal("user1"),
        )

    assert builder.commands == []


@pytest.mark.parametrize(
    ("source_type", "fetcher_name"),
    [
        (KBSourceType.WEB, "web_fetcher"),
        (KBSourceType.CONFLUENCE, "confluence_fetcher"),
        (KBSourceType.FEISHU, "feishu_fetcher"),
    ],
)
@pytest.mark.asyncio
async def test_reindex_hashes_fresh_typed_connector_content(
    monkeypatch,
    source_type,
    fetcher_name,
):
    url = "https://example.com/doc"
    kb = KnowledgeBase(
        id="kb1",
        active_version_id="v1",
        owner_user_id="user1",
    )
    document = KnowledgeDocument(
        id="d1",
        kb_id="kb1",
        title="old",
        source_type=source_type,
        source_ref=url,
    )
    revision = KnowledgeDocumentRevision(
        id="r1",
        document_id="d1",
        source_ref=url,
        source_digest=hashlib.sha256(b"old").hexdigest(),
    )
    uow = _ReindexUow(kb, document, revision)
    builder = _Builder(_candidate_result())
    connector = AsyncMock(
        return_value=WebDocument(
            title="fresh",
            content="fresh immutable content",
        )
    )
    monkeypatch.setattr(
        "app.application.services.knowledge_base_service.validate_public_url",
        lambda value: value,
    )
    service = KnowledgeBaseService(
        uow_factory=lambda: uow,
        file_storage=object(),
        version_builder=builder,
        build_dispatcher=AsyncMock(),
        **{fetcher_name: connector},
    )

    await service.reindex("kb1", scope=OwnerScope.personal("user1"))

    source = builder.commands[0][0].sources[0]
    assert source.source_digest == hashlib.sha256(
        b"fresh immutable content"
    ).hexdigest()
    assert source.source_identity == f"{source_type.value}:{url}"
    connector.assert_awaited_once_with(url)


@pytest.mark.parametrize(
    ("file_ids", "urls", "source_type"),
    [
        (["f1"], [], KBSourceType.WEB),
        ([], ["https://example.com"], KBSourceType.UPLOAD),
        (["f1"], ["https://example.com"], KBSourceType.UPLOAD),
    ],
)
@pytest.mark.asyncio
async def test_mixed_file_url_source_types_fail_before_opening_uow(
    file_ids,
    urls,
    source_type,
):
    opened = 0

    def factory():
        nonlocal opened
        opened += 1
        raise AssertionError("invalid source shape must fail before UoW")

    service = KnowledgeBaseService(
        uow_factory=factory,
        file_storage=object(),
    )
    with pytest.raises(BadRequestError):
        await service.add_documents(
            "kb1",
            file_ids=file_ids,
            urls=urls,
            source_type=source_type,
            scope=OwnerScope.personal("user1"),
        )
    assert opened == 0


@pytest.mark.parametrize("operation", ["add", "reindex", "remove"])
@pytest.mark.asyncio
async def test_missing_version_build_wiring_fails_closed_without_legacy_writes(
    operation,
):
    kb = KnowledgeBase(
        id="kb1",
        owner_user_id="user1",
        active_version_id=None,
    )
    file = File(
        id="f1",
        filename="doc.md",
        mime_type="text/markdown",
        owner_user_id="user1",
    )
    uow = _MutationSpyUow(kb, file)
    service = KnowledgeBaseService(
        uow_factory=lambda: uow,
        file_storage=SimpleNamespace(
            download_file=AsyncMock(
                return_value=(BytesIO(b"content"), file)
            )
        ),
        build_dispatcher=AsyncMock(),
    )
    scope = OwnerScope.personal("user1")

    with pytest.raises(RuntimeError, match="wiring is unavailable"):
        if operation == "add":
            await service.add_documents(
                "kb1",
                file_ids=["f1"],
                scope=scope,
            )
        elif operation == "reindex":
            await service.reindex("kb1", scope=scope)
        else:
            await service.delete_document("kb1", "d1", scope=scope)

    uow.knowledge_base.save_document.assert_not_awaited()
    uow.knowledge_base.delete_document.assert_not_awaited()
    uow.knowledge_base.clear_index_data.assert_not_awaited()
    uow.knowledge_base.mark_documents_pending.assert_not_awaited()
    uow.knowledge_base.save_kb.assert_not_awaited()


@pytest.mark.parametrize("operation", ["add", "reindex", "remove"])
@pytest.mark.asyncio
async def test_public_mutations_never_reach_destructive_repository_seams(
    operation,
):
    kb = KnowledgeBase(
        id="kb1",
        owner_user_id="user1",
        active_version_id=None,
    )
    file = File(
        id="f1",
        filename="doc.md",
        mime_type="text/markdown",
        owner_user_id="user1",
    )
    uow = _MutationSpyUow(kb, file)
    builder = _Builder(_candidate_result())
    service = KnowledgeBaseService(
        uow_factory=lambda: uow,
        file_storage=SimpleNamespace(
            download_file=AsyncMock(
                return_value=(BytesIO(b"content"), file)
            )
        ),
        version_builder=builder,
        build_dispatcher=AsyncMock(),
    )
    scope = OwnerScope.personal("user1")

    if operation == "add":
        await service.add_documents(
            "kb1",
            file_ids=["f1"],
            scope=scope,
        )
    elif operation == "reindex":
        await service.reindex("kb1", scope=scope)
    else:
        await service.delete_document("kb1", "d1", scope=scope)

    assert len(builder.commands) == 1
    uow.knowledge_base.save_document.assert_not_awaited()
    uow.knowledge_base.delete_document.assert_not_awaited()
    uow.knowledge_base.clear_index_data.assert_not_awaited()
    uow.knowledge_base.mark_documents_pending.assert_not_awaited()


class _VersionSurfaceUow:
    def __init__(self, kb, versions, builds):
        self.knowledge_base = SimpleNamespace(
            get_kb=AsyncMock(return_value=kb),
            count_ready_documents=AsyncMock(return_value={kb.id: 1}),
        )
        self.knowledge_version = SimpleNamespace(
            list_versions=AsyncMock(return_value=versions),
            get_version=AsyncMock(
                side_effect=lambda version_id, **_: next(
                    (
                        version
                        for version in versions
                        if version.id == version_id
                    ),
                    None,
                )
            ),
        )
        self.resource_governance = SimpleNamespace(
            get_build=AsyncMock(
                side_effect=lambda build_id, **_: builds.get(build_id)
            ),
            get_active_build=AsyncMock(
                return_value=next(
                    (
                        build
                        for build in builds.values()
                        if build.state
                        in {BuildState.QUEUED, BuildState.RUNNING}
                    ),
                    None,
                )
            ),
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_version_surface_keeps_active_published_and_candidate_distinct():
    now = datetime.now(timezone.utc)
    kb = KnowledgeBase(
        id="kb1",
        owner_user_id="user1",
        active_version_id="published",
    )
    published = KnowledgeBaseVersion(
        id="published",
        knowledge_base_id="kb1",
        state=KnowledgeVersionState.READY,
        published_at=now,
    )
    candidate = KnowledgeBaseVersion(
        id="candidate",
        knowledge_base_id="kb1",
        parent_version_id="published",
        build_id="build-active",
    )
    build = ResourceBuild(
        id="build-active",
        resource_kind=ResourceKind.KNOWLEDGE_BASE,
        resource_id="kb1",
        version_id="candidate",
        parent_version_id="published",
        command_key="command",
        state=BuildState.RUNNING,
        phase="chunk",
        progress=0.4,
        created_by="user1",
    )
    uow = _VersionSurfaceUow(
        kb,
        [candidate, published],
        {build.id: build},
    )
    service = KnowledgeBaseService(
        uow_factory=lambda: uow,
        file_storage=SimpleNamespace(),
    )

    history = await service.list_versions(
        "kb1",
        scope=OwnerScope.personal("user1"),
    )

    assert history.active_version_id == "published"
    assert history.active_build.id == "build-active"
    assert [version.id for version in history.versions] == [
        "candidate",
        "published",
    ]
    assert history.versions[0].is_candidate is True
    assert history.versions[0].is_published is False
    assert history.versions[0].build.phase == "chunk"
    assert history.versions[1].is_active is True
    assert history.versions[1].is_published is True
    detail = await service.get_version(
        "kb1",
        "published",
        scope=OwnerScope.personal("user1"),
    )
    assert detail.id == "published"
    assert detail.is_active is True
    with pytest.raises(NotFoundError):
        await service.get_version(
            "kb1",
            "foreign-version",
            scope=OwnerScope.personal("user1"),
        )


@pytest.mark.asyncio
async def test_cancel_requests_task_state_without_terminalizing_build():
    kb = KnowledgeBase(
        id="kb1",
        owner_user_id="user1",
        active_version_id="published",
    )
    candidate = KnowledgeBaseVersion(
        id="candidate",
        knowledge_base_id="kb1",
        parent_version_id="published",
        build_id="build-active",
    )
    build = ResourceBuild(
        id="build-active",
        resource_kind=ResourceKind.KNOWLEDGE_BASE,
        resource_id="kb1",
        version_id="candidate",
        parent_version_id="published",
        command_key="command",
        state=BuildState.QUEUED,
        created_by="user1",
    )
    uow = _VersionSurfaceUow(
        kb,
        [candidate],
        {build.id: build},
    )
    task_state = SimpleNamespace(request_cancel=AsyncMock())
    service = KnowledgeBaseService(
        uow_factory=lambda: uow,
        file_storage=SimpleNamespace(),
        task_state_port=task_state,
    )

    response = await service.cancel_build(
        "kb1",
        build.id,
        scope=OwnerScope.personal("user1"),
    )

    assert response.id == build.id
    assert response.state is BuildState.QUEUED
    assert response.can_cancel is True
    task_state.request_cancel.assert_awaited_once_with(build.id)
    assert build.state is BuildState.QUEUED

    terminal = build.model_copy(update={"state": BuildState.FAILED})
    uow.resource_governance.get_build.return_value = terminal
    uow.resource_governance.get_build.side_effect = None
    with pytest.raises(ConflictError, match="active"):
        await service.cancel_build(
            "kb1",
            build.id,
            scope=OwnerScope.personal("user1"),
        )

    uow.knowledge_base.get_kb.return_value = None
    with pytest.raises(NotFoundError):
        await service.cancel_build(
            "kb1",
            build.id,
            scope=OwnerScope.personal("attacker"),
        )
