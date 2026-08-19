#!/usr/bin/env python
# -*- coding: utf-8 -*-
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.domain.errors import NotFoundError
from app.domain.models.codebase import Codebase
from app.domain.models.codebase_version import (
    CodebaseVersion,
    CodebaseVersionState,
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
    version_id: str = "cbv1",
    *,
    resource_id: str = "cb1",
) -> ResourceBindingProjection:
    return ResourceBindingProjection(
        binding_id=f"binding-{version_id}",
        resource_kind=ResourceKind.CODEBASE,
        resource_id=resource_id,
        version_id=version_id,
    )


def _version(
    *,
    version_id: str = "cbv1",
    codebase_id: str = "cb1",
    state: CodebaseVersionState = CodebaseVersionState.READY,
    published: bool = True,
) -> CodebaseVersion:
    return CodebaseVersion(
        id=version_id,
        codebase_id=codebase_id,
        state=state,
        source_snapshot_key=f"snapshots/{version_id}.tgz",
        source_digest=f"digest-{version_id}",
        published_at=datetime.now(timezone.utc) if published else None,
    )


class _Uow:
    def __init__(self, version):
        self.codebase = AsyncMock()
        self.codebase.get_by_id.return_value = Codebase(
            id="cb1",
            name="Demo",
            active_version_id="cbv2",
        )
        self.codebase_version = AsyncMock()
        self.codebase_version.get_version.return_value = version
        self.knowledge_base = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


@pytest.mark.anyio
async def test_factory_authorizes_exact_session_codebase_binding_in_same_uow():
    uow = _Uow(_version())
    factory = _build_factory(AsyncMock())
    factory._uow_factory = lambda: uow
    session = Session(
        id="session1",
        resource_bindings=[_binding()],
        owner_user_id="user1",
    )

    codebase, version_id, _kb, _kb_version_id = (
        await factory._authorize_session_resources(
            session,
            factory._scope_for_session(session),
        )
    )

    assert codebase.id == "cb1"
    assert version_id == "cbv1"
    uow.codebase_version.get_version.assert_awaited_once_with(
        "cbv1", codebase_id="cb1"
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "bindings",
    [
        [_binding(), _binding("cbv2")],
        [_binding(resource_id="foreign-cb")],
    ],
)
async def test_factory_fails_closed_on_missing_duplicate_or_foreign_codebase_binding(
    bindings,
):
    factory = _build_factory(AsyncMock())
    factory._uow_factory = lambda: _Uow(_version())
    session = Session(
        id="session1",
        resource_bindings=bindings,
        owner_user_id="user1",
    )

    with pytest.raises(NotFoundError, match="版本绑定"):
        await factory._authorize_session_resources(
            session,
            factory._scope_for_session(session),
        )


@pytest.mark.anyio
async def test_factory_accepts_resource_free_session_without_binding():
    factory = _build_factory(AsyncMock())
    factory._uow_factory = lambda: _Uow(_version())
    session = Session(id="session1", owner_user_id="user1")

    authorized = await factory._authorize_session_resources(
        session,
        factory._scope_for_session(session),
    )

    assert authorized == (None, None, None, None)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "version",
    [
        None,
        _version(
            state=CodebaseVersionState.BUILDING,
            published=False,
        ),
        _version(
            state=CodebaseVersionState.FAILED,
            published=False,
        ),
    ],
)
async def test_factory_fails_closed_on_unpublished_codebase_binding(version):
    factory = _build_factory(AsyncMock())
    factory._uow_factory = lambda: _Uow(version)
    session = Session(
        id="session1",
        resource_bindings=[_binding()],
        owner_user_id="user1",
    )

    with pytest.raises(NotFoundError, match="已发布"):
        await factory._authorize_session_resources(
            session,
            factory._scope_for_session(session),
        )
