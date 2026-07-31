#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.application.errors.exceptions import NotFoundError
from app.domain.models.knowledge_base import KnowledgeBase
from app.domain.models.knowledge_version import (
    KnowledgeBaseVersion,
    KnowledgeVersionState,
)
from app.domain.models.resource_governance import (
    ResourceBindingProjection,
    ResourceKind,
)
from app.domain.models.session import Session
from tests.app.application.services.test_task_runner_factory import (
    _build_factory,
)


def _binding(
    version_id: str = "kbv1",
    *,
    resource_id: str = "kb1",
) -> ResourceBindingProjection:
    return ResourceBindingProjection(
        binding_id=f"binding-{version_id}",
        resource_kind=ResourceKind.KNOWLEDGE_BASE,
        resource_id=resource_id,
        version_id=version_id,
    )


def _version(
    *,
    version_id: str = "kbv1",
    kb_id: str = "kb1",
    state: KnowledgeVersionState = KnowledgeVersionState.READY,
    published: bool = True,
) -> KnowledgeBaseVersion:
    return KnowledgeBaseVersion(
        id=version_id,
        knowledge_base_id=kb_id,
        state=state,
        published_at=datetime.now(timezone.utc) if published else None,
    )


class _Uow:
    def __init__(self, version):
        self.codebase = AsyncMock()
        self.knowledge_base = AsyncMock()
        self.knowledge_base.get_kb.return_value = KnowledgeBase(
            id="kb1",
            name="Handbook",
            active_version_id="kbv2",
        )
        self.knowledge_version = AsyncMock()
        self.knowledge_version.get_version.return_value = version

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest.mark.anyio
async def test_factory_authorizes_exact_session_kb_binding_in_same_uow():
    uow = _Uow(_version())
    factory = _build_factory(AsyncMock())
    factory._uow_factory = lambda: uow
    session = Session(
        id="session1",
        knowledge_base_id="kb1",
        resource_bindings=[_binding()],
        owner_user_id="user1",
    )

    _codebase, _codebase_version_id, kb, version_id = await factory._authorize_session_resources(
        session,
        factory._scope_for_session(session),
    )

    assert kb.id == "kb1"
    assert version_id == "kbv1"
    uow.knowledge_version.get_version.assert_awaited_once_with(
        "kbv1", knowledge_base_id="kb1"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "bindings",
    [
        [],
        [_binding(), _binding("kbv2")],
        [_binding(resource_id="foreign-kb")],
    ],
)
async def test_factory_fails_closed_on_missing_duplicate_or_foreign_binding(
    bindings,
):
    factory = _build_factory(AsyncMock())
    factory._uow_factory = lambda: _Uow(_version())
    session = Session(
        id="session1",
        knowledge_base_id="kb1",
        resource_bindings=bindings,
        owner_user_id="user1",
    )

    with pytest.raises(NotFoundError, match="版本绑定"):
        await factory._authorize_session_resources(
            session,
            factory._scope_for_session(session),
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "version",
    [
        None,
        _version(
            state=KnowledgeVersionState.BUILDING,
            published=False,
        ),
        _version(
            state=KnowledgeVersionState.FAILED,
            published=False,
        ),
    ],
)
async def test_factory_fails_closed_on_unpublished_binding(version):
    factory = _build_factory(AsyncMock())
    factory._uow_factory = lambda: _Uow(version)
    session = Session(
        id="session1",
        knowledge_base_id="kb1",
        resource_bindings=[_binding()],
        owner_user_id="user1",
    )

    with pytest.raises(NotFoundError, match="已发布"):
        await factory._authorize_session_resources(
            session,
            factory._scope_for_session(session),
        )
