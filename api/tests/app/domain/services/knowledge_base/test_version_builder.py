#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Task 3 contracts for immutable candidate knowledge manifests."""
from __future__ import annotations

import copy
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.domain.errors import (
    BadRequestError,
    ConflictError,
    NotFoundError,
)
from app.application.services.knowledge_base_service import KnowledgeBaseService
from app.domain.models.file import File
from app.domain.models.knowledge_base import KBSourceType, KnowledgeBase, KnowledgeDocument
from app.domain.models.knowledge_version import (
    DocumentRevisionState,
    KnowledgeBaseVersion,
    KnowledgeDocumentRevision,
    KnowledgeVersionDocument,
    KnowledgeVersionState,
)
from app.domain.models.resource_governance import (
    BuildState,
    ResourceBuild,
    ResourceKind,
)
from app.domain.models.scope import OwnerScope
from app.domain.services.knowledge_base.version_builder import (
    KnowledgeBuildCommand,
    KnowledgeBuildSource,
    KnowledgeVersionBuilder,
    _retry_command_key,
)


def _source(
    identity: str,
    digest: str,
    *,
    document_id: str | None = None,
) -> KnowledgeBuildSource:
    return KnowledgeBuildSource(
        document_id=document_id,
        title=f"{identity}.md",
        source_type=KBSourceType.UPLOAD,
        source_ref=f'{{"file_id":"{identity}"}}',
        source_identity=f"file:{identity}",
        source_digest=digest,
        mime="text/markdown",
        file_id=identity,
    )


class _State:
    def __init__(self) -> None:
        self.kbs = {
            "kb1": KnowledgeBase(
                id="kb1",
                name="KB",
                owner_user_id="user1",
                active_version_id="v1",
            )
        }
        self.documents = {
            "d1": KnowledgeDocument(
                id="d1",
                kb_id="kb1",
                title="one.md",
                source_ref='{"file_id":"f1"}',
                file_id="f1",
            ),
            "d2": KnowledgeDocument(
                id="d2",
                kb_id="kb1",
                title="two.md",
                source_ref='{"file_id":"f2"}',
                file_id="f2",
            ),
        }
        self.versions = {
            "v1": KnowledgeBaseVersion(
                id="v1",
                knowledge_base_id="kb1",
                state=KnowledgeVersionState.READY,
            )
        }
        self.revisions = {
            "r1": KnowledgeDocumentRevision(
                id="r1", document_id="d1", source_ref='{"file_id":"f1"}',
                source_digest="a" * 64, state=DocumentRevisionState.INDEXED,
            ),
            "r2": KnowledgeDocumentRevision(
                id="r2", document_id="d2", source_ref='{"file_id":"f2"}',
                source_digest="b" * 64, state=DocumentRevisionState.INDEXED,
            ),
        }
        self.manifests = {
            "v1": [
                KnowledgeVersionDocument(
                    version_id="v1", document_id="d1",
                    document_revision_id="r1", ordinal=0,
                    state=DocumentRevisionState.INDEXED,
                ),
                KnowledgeVersionDocument(
                    version_id="v1", document_id="d2",
                    document_revision_id="r2", ordinal=1,
                    state=DocumentRevisionState.INDEXED,
                ),
            ]
        }
        self.index_rows = {
            "v1": (
                ("chunk-r1", "r1", b"stable-index-one"),
                ("chunk-r2", "r2", b"stable-index-two"),
            )
        }
        self.builds: dict[str, ResourceBuild] = {}


class _KbRepo:
    def __init__(self, state: _State) -> None:
        self.state = state

    async def get_kb(self, kb_id, scope=None):
        kb = self.state.kbs.get(kb_id)
        if kb is None or scope is None or kb.owner_user_id != scope.user_id:
            return None
        return kb

    async def get_kb_for_update(self, kb_id, scope=None):
        return await self.get_kb(kb_id, scope=scope)

    async def save_kb(self, kb):
        self.state.kbs[kb.id] = kb

    async def count_ready_documents(self, kb_ids):
        return {kb_id: 0 for kb_id in kb_ids}

    async def get_document(self, document_id):
        return self.state.documents.get(document_id)

    async def save_document(self, document):
        self.state.documents[document.id] = document

    async def insert_document(self, document):
        if document.id in self.state.documents:
            raise IntegrityError(
                "INSERT knowledge_documents",
                {},
                RuntimeError("knowledge_documents_pkey"),
            )
        self.state.documents[document.id] = document


class _VersionRepo:
    def __init__(self, state: _State) -> None:
        self.state = state

    async def create_candidate(self, version):
        self.state.versions[version.id] = version
        return version

    async def get_version(self, version_id, *, knowledge_base_id):
        version = self.state.versions.get(version_id)
        return version if version and version.knowledge_base_id == knowledge_base_id else None

    async def get_manifest(self, version_id, *, knowledge_base_id):
        version = await self.get_version(version_id, knowledge_base_id=knowledge_base_id)
        return list(self.state.manifests.get(version_id, ())) if version else []

    async def add_revision(self, revision, *, knowledge_base_id):
        document = self.state.documents.get(revision.document_id)
        if document is None or document.kb_id != knowledge_base_id:
            raise ValueError("revision owner mismatch")
        self.state.revisions[revision.id] = revision
        return revision

    async def get_revision_by_digest(
        self,
        document_id,
        source_digest,
        *,
        knowledge_base_id,
    ):
        document = self.state.documents.get(document_id)
        if document is None or document.kb_id != knowledge_base_id:
            return None
        return next(
            (
                revision
                for revision in self.state.revisions.values()
                if revision.document_id == document_id
                and revision.source_digest == source_digest
            ),
            None,
        )

    async def get_revisions(self, revision_ids, *, knowledge_base_id):
        document_ids = {
            document.id
            for document in self.state.documents.values()
            if document.kb_id == knowledge_base_id
        }
        return {
            revision_id: self.state.revisions[revision_id]
            for revision_id in revision_ids
            if revision_id in self.state.revisions
            and self.state.revisions[revision_id].document_id in document_ids
        }

    async def add_manifest(self, version_id, documents, *, knowledge_base_id):
        assert version_id in self.state.versions
        self.state.manifests[version_id] = list(documents)


class _BuildRepo:
    def __init__(self, state: _State) -> None:
        self.state = state

    async def get_active_build(self, resource_kind, resource_id):
        return next(
            (
                build for build in self.state.builds.values()
                if build.resource_kind == resource_kind
                and build.resource_id == resource_id
                and build.state in {BuildState.QUEUED, BuildState.RUNNING}
            ),
            None,
        )

    async def get_build(self, build_id, *, for_update=False):
        return self.state.builds.get(build_id)

    async def add_build(self, build):
        if await self.get_active_build(build.resource_kind, build.resource_id):
            raise RuntimeError("active build uniqueness")
        self.state.builds[build.id] = build
        return build


class _FileRepo:
    async def get_by_id(self, file_id, scope=None):
        if scope is None or scope.user_id != "user1":
            return None
        return File(
            id=file_id,
            filename=f"{file_id}.md",
            mime_type="text/markdown",
            owner_user_id="user1",
        )


class _Uow:
    def __init__(self, state: _State, *, exit_error: Exception | None = None) -> None:
        self.state = state
        self.exit_error = exit_error
        self.knowledge_base = _KbRepo(state)
        self.knowledge_version = _VersionRepo(state)
        self.resource_governance = _BuildRepo(state)
        self.file = _FileRepo()
        self._snapshot = None

    async def __aenter__(self):
        self._snapshot = copy.deepcopy(self.state.__dict__)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None or self.exit_error is not None:
            self.state.__dict__.clear()
            self.state.__dict__.update(self._snapshot)
        if exc_type is None and self.exit_error is not None:
            raise self.exit_error
        return False


class _PublishBeforeRetryLockKbRepo(_KbRepo):
    def __init__(self, state: _State) -> None:
        super().__init__(state)
        self.lock_calls = 0

    async def get_kb_for_update(self, kb_id, scope=None):
        self.lock_calls += 1
        current = self.state.kbs[kb_id]
        self.state.kbs[kb_id] = current.model_copy(
            update={"active_version_id": "v2"}
        )
        return await super().get_kb(kb_id, scope=scope)


class _PublishBeforeRetryLockUow(_Uow):
    def __init__(self, state: _State) -> None:
        super().__init__(state)
        self.knowledge_base = _PublishBeforeRetryLockKbRepo(state)


class _RacingBuildRepo(_BuildRepo):
    async def add_build(self, build):
        raise IntegrityError(
            "INSERT resource_builds",
            {},
            RuntimeError("uq_resource_builds_active"),
        )


class _UnrelatedIntegrityBuildRepo(_BuildRepo):
    async def add_build(self, build):
        raise IntegrityError(
            "INSERT resource_builds",
            {},
            RuntimeError("uq_unrelated_revision_or_manifest"),
        )


class _UnrelatedIntegrityUow(_Uow):
    def __init__(self, state):
        super().__init__(state)
        self.resource_governance = _UnrelatedIntegrityBuildRepo(state)


class _FailingVersionRepo(_VersionRepo):
    def __init__(self, state, stage):
        super().__init__(state)
        self.stage = stage

    async def add_revision(self, revision, *, knowledge_base_id):
        if self.stage == "revision":
            raise IntegrityError(
                "INSERT knowledge_document_revisions",
                {},
                RuntimeError("uq_unrelated_revision"),
            )
        return await super().add_revision(
            revision,
            knowledge_base_id=knowledge_base_id,
        )

    async def add_manifest(self, version_id, documents, *, knowledge_base_id):
        if self.stage == "manifest":
            raise IntegrityError(
                "INSERT knowledge_version_documents",
                {},
                RuntimeError("fk_unrelated_manifest"),
            )
        return await super().add_manifest(
            version_id,
            documents,
            knowledge_base_id=knowledge_base_id,
        )


class _FailingVersionUow(_Uow):
    def __init__(self, state, stage):
        super().__init__(state)
        self.knowledge_version = _FailingVersionRepo(state, stage)


class _RaceLoserUow(_Uow):
    def __init__(self, state, *, winner_key, lifecycle):
        super().__init__(state)
        self.resource_governance = _RacingBuildRepo(state)
        self.winner_key = winner_key
        self.lifecycle = lifecycle

    async def __aenter__(self):
        self.lifecycle.append("loser-enter")
        return await super().__aenter__()

    async def __aexit__(self, exc_type, exc, tb):
        result = await super().__aexit__(exc_type, exc, tb)
        self.lifecycle.append("loser-exit")
        if exc_type is IntegrityError:
            winner = ResourceBuild(
                id="winner-build",
                resource_kind=ResourceKind.KNOWLEDGE_BASE,
                resource_id="kb1",
                version_id="winner-version",
                parent_version_id="v1",
                command_key=self.winner_key,
                created_by="user1",
            )
            self.state.builds[winner.id] = winner
            self.state.versions[winner.version_id] = KnowledgeBaseVersion(
                id=winner.version_id,
                knowledge_base_id="kb1",
                parent_version_id="v1",
                build_id=winner.id,
            )
        return result


@pytest.fixture
def setup_builder():
    state = _State()
    builder = KnowledgeVersionBuilder(lambda: _Uow(state))
    return state, builder


def test_command_key_is_order_independent_and_options_are_deeply_immutable():
    first = KnowledgeBuildCommand.add(
        "kb1",
        [_source("f4", "d" * 64), _source("f3", "c" * 64)],
        actor_id="user1",
        options={"nested": {"modes": ["bm25", "vector"]}},
    )
    second = KnowledgeBuildCommand.add(
        "kb1",
        [_source("f3", "c" * 64), _source("f4", "d" * 64)],
        actor_id="user1",
        options={"nested": {"modes": ["bm25", "vector"]}},
    )
    assert first == second
    assert first.command_key(
        owner_identity="user:user1",
        base_version_id="v1",
    ) == second.command_key(
        owner_identity="user:user1",
        base_version_id="v1",
    )
    assert isinstance(first.options[0][1], tuple)
    with pytest.raises(Exception):
        first.sources += (_source("f5", "e" * 64),)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "title": "missing-file.md",
            "source_type": KBSourceType.UPLOAD,
            "source_ref": '{"file_id":"f1"}',
            "source_identity": "file:f1",
            "source_digest": "a" * 64,
        },
        {
            "title": "wrong-ref.md",
            "source_type": KBSourceType.ZIP,
            "source_ref": '{"file_id":"other"}',
            "source_identity": "file:f1",
            "source_digest": "a" * 64,
            "file_id": "f1",
        },
        {
            "title": "mixed.md",
            "source_type": KBSourceType.WEB,
            "source_ref": "https://example.com/doc",
            "source_identity": "web:https://example.com/doc",
            "source_digest": "a" * 64,
            "file_id": "f1",
        },
        {
            "title": "wrong-identity.md",
            "source_type": KBSourceType.CONFLUENCE,
            "source_ref": "https://example.com/doc",
            "source_identity": "web:https://example.com/doc",
            "source_digest": "a" * 64,
        },
    ],
)
def test_source_shape_rejects_missing_or_mixed_canonical_identity(payload):
    with pytest.raises(ValidationError):
        KnowledgeBuildSource.model_validate(payload)


def test_add_forbids_caller_supplied_logical_document_id():
    with pytest.raises(ValidationError, match="document id"):
        KnowledgeBuildCommand.add(
            "kb1",
            [_source("f3", "c" * 64, document_id="historical-id")],
            actor_id="user1",
        )


@pytest.mark.asyncio
async def test_private_url_is_rejected_before_candidate_uow_opens(monkeypatch):
    opened = 0

    def factory():
        nonlocal opened
        opened += 1
        raise AssertionError("private URL must fail before write UoW")

    monkeypatch.setattr(
        "app.domain.services.knowledge_base.version_builder.validate_public_url",
        lambda _value: (_ for _ in ()).throw(
            BadRequestError("private target")
        ),
    )
    builder = KnowledgeVersionBuilder(factory)
    source = KnowledgeBuildSource(
        title="private",
        source_type=KBSourceType.WEB,
        source_ref="http://169.254.169.254/latest/meta-data",
        source_identity=(
            "web:http://169.254.169.254/latest/meta-data"
        ),
        source_digest="a" * 64,
    )

    with pytest.raises(BadRequestError, match="private target"):
        await builder.create_candidate(
            KnowledgeBuildCommand.add(
                "kb1",
                [source],
                actor_id="user1",
            ),
            scope=OwnerScope.personal("user1"),
        )
    assert opened == 0


@pytest.mark.asyncio
async def test_add_is_copy_on_write_and_deterministically_appends(setup_builder):
    state, builder = setup_builder
    result = await builder.create_candidate(
        KnowledgeBuildCommand.add(
            "kb1",
            [_source("f4", "d" * 64), _source("f3", "c" * 64)],
            actor_id="user1",
        ),
        scope=OwnerScope.personal("user1"),
    )

    candidate_manifest = state.manifests[result.version.id]
    assert [entry.document_revision_id for entry in candidate_manifest[:2]] == ["r1", "r2"]
    assert [state.documents[entry.document_id].file_id for entry in candidate_manifest[2:]] == ["f3", "f4"]
    assert [entry.ordinal for entry in candidate_manifest] == [0, 1, 2, 3]
    assert all(entry.state is DocumentRevisionState.UPLOADED for entry in candidate_manifest[2:])
    assert state.manifests["v1"][0].document_revision_id == "r1"
    assert result.version.state is KnowledgeVersionState.BUILDING
    assert result.build.version_id == result.version.id
    assert result.build.parent_version_id == "v1"
    assert result.created is True


@pytest.mark.asyncio
async def test_remove_never_deletes_logical_document_revision_or_old_manifest(setup_builder):
    state, builder = setup_builder
    before = copy.deepcopy(state.manifests["v1"])
    result = await builder.create_candidate(
        KnowledgeBuildCommand.remove("kb1", "d1", actor_id="user1"),
        scope=OwnerScope.personal("user1"),
    )

    assert [entry.document_id for entry in state.manifests[result.version.id]] == ["d2"]
    assert state.manifests["v1"] == before
    assert "d1" in state.documents
    assert "r1" in state.revisions


@pytest.mark.asyncio
async def test_replace_preserves_logical_identity_and_reindex_reuses_unchanged_digest(setup_builder):
    state, builder = setup_builder
    old_logical_document = state.documents["d1"].model_copy(deep=True)
    replaced = await builder.create_candidate(
        KnowledgeBuildCommand.replace(
            "kb1", "d1", _source("f1", "e" * 64, document_id="d1"),
            actor_id="user1",
        ),
        scope=OwnerScope.personal("user1"),
    )
    replacement_manifest = state.manifests[replaced.version.id]
    assert replacement_manifest[0].document_id == "d1"
    assert replacement_manifest[0].document_revision_id != "r1"
    assert replacement_manifest[1].document_revision_id == "r2"
    assert state.documents["d1"] == old_logical_document

    state.builds[replaced.build.id] = replaced.build.model_copy(
        update={"state": BuildState.SUCCEEDED}
    )
    state.kbs["kb1"].active_version_id = replaced.version.id
    state.versions[replaced.version.id] = replaced.version.model_copy(
        update={"state": KnowledgeVersionState.READY}
    )
    reindexed = await builder.create_candidate(
        KnowledgeBuildCommand.reindex(
            "kb1",
            [
                _source("f1", "e" * 64, document_id="d1"),
                _source("f2", "f" * 64, document_id="d2"),
            ],
            actor_id="user1",
        ),
        scope=OwnerScope.personal("user1"),
    )
    next_manifest = state.manifests[reindexed.version.id]
    assert next_manifest[0].document_revision_id == replacement_manifest[0].document_revision_id
    assert next_manifest[1].document_revision_id != "r2"


@pytest.mark.asyncio
async def test_replace_same_content_reuses_existing_revision(setup_builder):
    state, builder = setup_builder
    result = await builder.create_candidate(
        KnowledgeBuildCommand.replace(
            "kb1",
            "d1",
            _source("f-new", "a" * 64, document_id="d1"),
            actor_id="user1",
        ),
        scope=OwnerScope.personal("user1"),
    )

    manifest = state.manifests[result.version.id]
    assert manifest[0].document_revision_id == "r1"
    assert set(state.revisions) == {"r1", "r2"}


@pytest.mark.asyncio
async def test_reindex_a_to_b_to_a_reuses_historical_revision(setup_builder):
    state, builder = setup_builder
    changed = await builder.create_candidate(
        KnowledgeBuildCommand.reindex(
            "kb1",
            [_source("f1", "c" * 64, document_id="d1")],
            actor_id="user1",
        ),
        scope=OwnerScope.personal("user1"),
    )
    changed_revision_id = state.manifests[changed.version.id][0].document_revision_id
    assert changed_revision_id not in {"r1", "r2"}

    state.builds[changed.build.id] = changed.build.model_copy(
        update={"state": BuildState.SUCCEEDED}
    )
    state.kbs["kb1"].active_version_id = changed.version.id
    state.versions[changed.version.id] = changed.version.model_copy(
        update={"state": KnowledgeVersionState.READY}
    )
    reverted = await builder.create_candidate(
        KnowledgeBuildCommand.reindex(
            "kb1",
            [_source("f1", "a" * 64, document_id="d1")],
            actor_id="user1",
        ),
        scope=OwnerScope.personal("user1"),
    )

    assert state.manifests[reverted.version.id][0].document_revision_id == "r1"
    assert len(
        [
            revision
            for revision in state.revisions.values()
            if revision.document_id == "d1"
            and revision.source_digest == "a" * 64
        ]
    ) == 1


@pytest.mark.asyncio
async def test_exact_duplicate_returns_winner_but_different_active_command_conflicts(setup_builder):
    state, builder = setup_builder
    command = KnowledgeBuildCommand.add(
        "kb1", [_source("f3", "c" * 64)], actor_id="user1"
    )
    winner = await builder.create_candidate(
        command, scope=OwnerScope.personal("user1")
    )
    duplicate = await builder.create_candidate(
        command, scope=OwnerScope.personal("user1")
    )
    assert duplicate.created is False
    assert duplicate.build.id == winner.build.id
    assert duplicate.version.id == winner.version.id
    assert len(state.versions) == 2

    with pytest.raises(ConflictError, match="different"):
        await builder.create_candidate(
            KnowledgeBuildCommand.remove("kb1", "d1", actor_id="user1"),
            scope=OwnerScope.personal("user1"),
        )


@pytest.mark.asyncio
async def test_integrity_race_exits_failed_uow_before_fresh_winner_read():
    state = _State()
    command = KnowledgeBuildCommand.add(
        "kb1", [_source("f3", "c" * 64)], actor_id="user1"
    )
    winner_key = command.command_key(
        owner_identity="user:user1",
        base_version_id="v1",
    )
    lifecycle = []
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        if calls == 1:
            return _RaceLoserUow(
                state,
                winner_key=winner_key,
                lifecycle=lifecycle,
            )
        lifecycle.append("winner-uow-created")
        return _Uow(state)

    result = await KnowledgeVersionBuilder(factory).create_candidate(
        command,
        scope=OwnerScope.personal("user1"),
    )

    assert result.created is False
    assert result.build.id == "winner-build"
    assert lifecycle == [
        "loser-enter",
        "loser-exit",
        "winner-uow-created",
    ]
    assert set(state.documents) == {"d1", "d2"}
    assert set(state.revisions) == {"r1", "r2"}


@pytest.mark.asyncio
async def test_unrelated_integrity_error_is_not_mapped_to_active_build_race():
    state = _State()
    before = copy.deepcopy(state.__dict__)
    builder = KnowledgeVersionBuilder(lambda: _UnrelatedIntegrityUow(state))

    with pytest.raises(IntegrityError) as caught:
        await builder.create_candidate(
            KnowledgeBuildCommand.add(
                "kb1",
                [_source("f3", "c" * 64)],
                actor_id="user1",
            ),
            scope=OwnerScope.personal("user1"),
        )

    assert "uq_unrelated_revision_or_manifest" in str(caught.value.orig)
    assert state.__dict__ == before


@pytest.mark.parametrize("stage", ["revision", "manifest"])
@pytest.mark.asyncio
async def test_revision_or_manifest_integrity_failure_rolls_back_whole_graph(
    stage,
):
    state = _State()
    before = copy.deepcopy(state.__dict__)
    builder = KnowledgeVersionBuilder(
        lambda: _FailingVersionUow(state, stage)
    )

    with pytest.raises(IntegrityError):
        await builder.create_candidate(
            KnowledgeBuildCommand.add(
                "kb1",
                [_source("f3", "c" * 64)],
                actor_id="user1",
            ),
            scope=OwnerScope.personal("user1"),
        )

    assert state.__dict__ == before


@pytest.mark.asyncio
async def test_duplicate_sources_and_foreign_scope_are_zero_effect(setup_builder):
    state, builder = setup_builder
    before = copy.deepcopy(state.__dict__)
    with pytest.raises(ValueError, match="duplicate source"):
        KnowledgeBuildCommand.add(
            "kb1",
            [_source("f3", "c" * 64), _source("f3", "c" * 64)],
            actor_id="user1",
        )
    with pytest.raises(NotFoundError):
        await builder.create_candidate(
            KnowledgeBuildCommand.remove("kb1", "d1", actor_id="attacker"),
            scope=OwnerScope.personal("attacker"),
        )
    assert state.__dict__ == before


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_complete_candidate_graph():
    state = _State()
    builder = KnowledgeVersionBuilder(
        lambda: _Uow(state, exit_error=RuntimeError("commit failed"))
    )
    before = copy.deepcopy(state.__dict__)
    with pytest.raises(RuntimeError, match="commit failed"):
        await builder.create_candidate(
            KnowledgeBuildCommand.add(
                "kb1", [_source("f3", "c" * 64)], actor_id="user1"
            ),
            scope=OwnerScope.personal("user1"),
        )
    assert state.__dict__ == before


@pytest.mark.asyncio
async def test_add_to_empty_kb_creates_only_candidate_rows():
    state = _State()
    state.kbs["kb1"].active_version_id = None
    state.versions.clear()
    state.manifests.clear()
    state.documents.clear()
    state.revisions.clear()
    builder = KnowledgeVersionBuilder(lambda: _Uow(state))

    result = await builder.create_candidate(
        KnowledgeBuildCommand.add(
            "kb1",
            [_source("f1", "a" * 64)],
            actor_id="user1",
        ),
        scope=OwnerScope.personal("user1"),
    )

    assert result.version.parent_version_id is None
    assert len(state.manifests[result.version.id]) == 1
    assert result.resource.active_version_id is None


def _publish_candidate(state, result):
    state.builds[result.build.id] = result.build.model_copy(
        update={"state": BuildState.SUCCEEDED}
    )
    state.versions[result.version.id] = result.version.model_copy(
        update={"state": KnowledgeVersionState.READY}
    )
    state.kbs["kb1"].active_version_id = result.version.id


@pytest.mark.asyncio
async def test_actual_service_remove_then_readd_preserves_complete_history():
    state = _State()
    old_document = state.documents["d1"].model_copy(deep=True)
    old_revision = state.revisions["r1"].model_copy(deep=True)
    old_manifest = copy.deepcopy(state.manifests["v1"])
    old_version = state.versions["v1"].model_copy(deep=True)
    old_index_rows = copy.deepcopy(state.index_rows["v1"])
    dispatch = AsyncMock()
    storage = SimpleNamespace(
        download_file=AsyncMock(
            side_effect=lambda file_id: (
                BytesIO(
                    b"original-one"
                    if file_id == "f1"
                    else b"replacement"
                ),
                File(
                    id=file_id,
                    filename=f"{file_id}.md",
                    mime_type="text/markdown",
                    owner_user_id="user1",
                ),
            )
        )
    )
    service = KnowledgeBaseService(
        uow_factory=lambda: _Uow(state),
        file_storage=storage,
        version_builder=KnowledgeVersionBuilder(lambda: _Uow(state)),
        build_dispatcher=dispatch,
    )
    scope = OwnerScope.personal("user1")

    removed_resource = await service.delete_document(
        "kb1",
        "d1",
        scope=scope,
    )
    removed_build = next(
        build
        for build in state.builds.values()
        if build.id == removed_resource.ingest_task_id
    )
    removed_result = SimpleNamespace(
        build=removed_build,
        version=state.versions[removed_build.version_id],
    )
    _publish_candidate(state, removed_result)

    readded_resource = await service.add_documents(
        "kb1",
        file_ids=["f1"],
        source_type=KBSourceType.UPLOAD,
        scope=scope,
    )
    readded_manifest = state.manifests[
        state.builds[readded_resource.ingest_task_id].version_id
    ]

    assert [entry.document_id for entry in readded_manifest][0] == "d2"
    assert readded_manifest[1].document_id != "d1"
    assert state.documents["d1"] == old_document
    assert state.revisions["r1"] == old_revision
    assert state.manifests["v1"] == old_manifest
    assert state.versions["v1"] == old_version
    assert state.index_rows["v1"] == old_index_rows
    assert dispatch.await_count == 2


@pytest.mark.asyncio
async def test_actual_service_replace_and_reindex_never_destructively_mutate():
    state = _State()
    storage_payloads = {
        "f2": b"original-two",
        "f3": b"replacement-v1",
    }
    storage = SimpleNamespace(
        download_file=AsyncMock(
            side_effect=lambda file_id: (
                BytesIO(storage_payloads[file_id]),
                File(
                    id=file_id,
                    filename=f"{file_id}.md",
                    mime_type="text/markdown",
                    owner_user_id="user1",
                ),
            )
        )
    )
    dispatch = AsyncMock()
    service = KnowledgeBaseService(
        uow_factory=lambda: _Uow(state),
        file_storage=storage,
        version_builder=KnowledgeVersionBuilder(lambda: _Uow(state)),
        build_dispatcher=dispatch,
    )
    scope = OwnerScope.personal("user1")
    original_document = state.documents["d1"].model_copy(deep=True)
    original_manifest = copy.deepcopy(state.manifests["v1"])

    replaced_resource = await service.replace_document(
        "kb1",
        "d1",
        file_id="f3",
        scope=scope,
    )
    replaced_build = state.builds[replaced_resource.ingest_task_id]
    replaced_result = SimpleNamespace(
        build=replaced_build,
        version=state.versions[replaced_build.version_id],
    )
    _publish_candidate(state, replaced_result)
    replaced_revision_id = state.manifests[
        replaced_build.version_id
    ][0].document_revision_id

    storage_payloads["f3"] = b"replacement-v2"
    reindexed_resource = await service.reindex("kb1", scope=scope)
    reindexed_build = state.builds[reindexed_resource.ingest_task_id]
    reindexed_revision_id = state.manifests[
        reindexed_build.version_id
    ][0].document_revision_id

    assert replaced_revision_id != "r1"
    assert reindexed_revision_id not in {"r1", replaced_revision_id}
    assert state.documents["d1"] == original_document
    assert state.manifests["v1"] == original_manifest
    assert set(state.documents) == {"d1", "d2"}
    assert dispatch.await_count == 2


def _failed_candidate(state: _State, *, parent_version_id: str = "v1"):
    build = ResourceBuild(
        id="failed-build",
        resource_kind=ResourceKind.KNOWLEDGE_BASE,
        resource_id="kb1",
        version_id="failed-version",
        parent_version_id=parent_version_id,
        command_key="original-command",
        state=BuildState.FAILED,
        created_by="user1",
    )
    version = KnowledgeBaseVersion(
        id=build.version_id,
        knowledge_base_id="kb1",
        parent_version_id=parent_version_id,
        build_id=build.id,
        state=KnowledgeVersionState.FAILED,
    )
    manifest = [
        item.model_copy(
            update={
                "version_id": version.id,
                "state": (
                    DocumentRevisionState.FAILED
                    if index == 0
                    else DocumentRevisionState.PARSING
                ),
                "error": "parse failed" if index == 0 else None,
                "warning": "retry me" if index == 1 else None,
            }
        )
        for index, item in enumerate(state.manifests["v1"])
    ]
    state.builds[build.id] = build
    state.versions[version.id] = version
    state.manifests[version.id] = manifest
    return build, version, manifest


@pytest.mark.asyncio
async def test_retry_failed_candidate_creates_fresh_build_and_exact_manifest_clone():
    state = _State()
    original_build, original_version, original_manifest = _failed_candidate(
        state
    )
    builder = KnowledgeVersionBuilder(lambda: _Uow(state))

    result = await builder.retry_candidate(
        "kb1",
        original_build.id,
        actor_id="user1",
        scope=OwnerScope.personal("user1"),
    )

    assert result.created is True
    assert result.build.id != original_build.id
    assert result.version.id != original_version.id
    assert result.build.version_id == result.version.id
    assert result.build.parent_version_id == "v1"
    assert result.version.parent_version_id == "v1"
    assert result.build.state is BuildState.QUEUED
    assert result.version.state is KnowledgeVersionState.BUILDING
    assert state.kbs["kb1"].active_version_id == "v1"
    cloned = state.manifests[result.version.id]
    assert [
        item.model_dump(exclude={"version_id"}) for item in cloned
    ] == [
        item.model_dump(exclude={"version_id"})
        for item in original_manifest
    ]
    assert all(item.version_id == result.version.id for item in cloned)
    assert state.manifests[original_version.id] == original_manifest


@pytest.mark.asyncio
async def test_retry_rejects_stale_parent_and_non_terminal_or_foreign_builds():
    state = _State()
    stale_build, _, _ = _failed_candidate(
        state,
        parent_version_id="older-active",
    )
    before = copy.deepcopy(state.__dict__)
    builder = KnowledgeVersionBuilder(lambda: _Uow(state))

    with pytest.raises(ConflictError, match="active version"):
        await builder.retry_candidate(
            "kb1",
            stale_build.id,
            actor_id="user1",
            scope=OwnerScope.personal("user1"),
        )
    assert state.__dict__ == before

    with pytest.raises(NotFoundError):
        await builder.retry_candidate(
            "kb1",
            stale_build.id,
            actor_id="attacker",
            scope=OwnerScope.personal("attacker"),
        )

    state.builds[stale_build.id] = stale_build.model_copy(
        update={"state": BuildState.SUCCEEDED}
    )
    with pytest.raises(ConflictError, match="failed or cancelled"):
        await builder.retry_candidate(
            "kb1",
            stale_build.id,
            actor_id="user1",
            scope=OwnerScope.personal("user1"),
        )


@pytest.mark.asyncio
async def test_retry_rechecks_active_pin_under_kb_row_lock_before_insert():
    state = _State()
    original, _, _ = _failed_candidate(state)
    uow = _PublishBeforeRetryLockUow(state)
    builder = KnowledgeVersionBuilder(lambda: uow)
    before_build_ids = set(state.builds)
    before_version_ids = set(state.versions)

    with pytest.raises(ConflictError, match="active version"):
        await builder.retry_candidate(
            "kb1",
            original.id,
            actor_id="user1",
            scope=OwnerScope.personal("user1"),
        )

    assert uow.knowledge_base.lock_calls == 1
    assert set(state.builds) == before_build_ids
    assert set(state.versions) == before_version_ids


@pytest.mark.asyncio
async def test_concurrent_duplicate_retry_resolves_deterministic_active_winner():
    state = _State()
    original, _, _ = _failed_candidate(state)
    retry_key = _retry_command_key(
        original,
        owner_identity="user:user1",
        active_version_id="v1",
    )
    lifecycle = []
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        if calls == 1:
            return _RaceLoserUow(
                state,
                winner_key=retry_key,
                lifecycle=lifecycle,
            )
        lifecycle.append("winner-uow-created")
        return _Uow(state)

    builder = KnowledgeVersionBuilder(factory)
    result = await builder.retry_candidate(
        "kb1",
        original.id,
        actor_id="user1",
        scope=OwnerScope.personal("user1"),
    )

    assert result.created is False
    assert result.build.id == "winner-build"
    assert result.build.command_key == retry_key
    assert lifecycle == [
        "loser-enter",
        "loser-exit",
        "winner-uow-created",
    ]

    duplicate = await builder.retry_candidate(
        "kb1",
        original.id,
        actor_id="user1",
        scope=OwnerScope.personal("user1"),
    )
    assert duplicate.created is False
    assert duplicate.build.id == result.build.id


@pytest.mark.asyncio
async def test_retry_rejects_malformed_candidate_and_manifest_closures():
    state = _State()
    original, version, _ = _failed_candidate(state)
    builder = KnowledgeVersionBuilder(lambda: _Uow(state))

    state.versions[version.id] = version.model_copy(
        update={"build_id": "other-build"}
    )
    with pytest.raises(ConflictError, match="candidate closure"):
        await builder.retry_candidate(
            "kb1",
            original.id,
            actor_id="user1",
            scope=OwnerScope.personal("user1"),
        )

    state.versions[version.id] = version
    state.manifests[version.id][1] = state.manifests[version.id][1].model_copy(
        update={"ordinal": 0}
    )
    with pytest.raises(ConflictError, match="manifest closure"):
        await builder.retry_candidate(
            "kb1",
            original.id,
            actor_id="user1",
            scope=OwnerScope.personal("user1"),
        )
