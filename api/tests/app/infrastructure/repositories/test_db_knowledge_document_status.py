"""Task 7 RED tests for explicit knowledge-document patch semantics."""

from __future__ import annotations

import importlib
import inspect
from types import SimpleNamespace

import pytest

from app.domain.models.knowledge_base import DocStatus
from app.domain.models.knowledge_version import (
    DocumentRevisionState,
    KnowledgeVersionState,
)
from app.infrastructure.repositories.db_knowledge_base_repository import (
    DBKnowledgeBaseRepository,
)
from app.infrastructure.repositories.db_knowledge_version_repository import (
    DBKnowledgeVersionRepository,
)


class _ExecuteRecorder:
    def __init__(self) -> None:
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace()


def _compiled_values(statement) -> dict:
    return dict(statement.compile().params)


@pytest.mark.anyio
async def test_logical_document_omitted_diagnostics_are_preserved():
    session = _ExecuteRecorder()
    repo = DBKnowledgeBaseRepository(session)

    await repo.update_document_status("doc1", DocStatus.PARSING)

    values = _compiled_values(session.statements[-1])
    assert "error" not in values
    assert "warning" not in values


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["error", "warning"])
async def test_logical_document_explicit_none_writes_sql_null(field):
    session = _ExecuteRecorder()
    repo = DBKnowledgeBaseRepository(session)

    await repo.update_document_status(
        "doc1",
        DocStatus.PARSING,
        **{field: None},
    )

    values = _compiled_values(session.statements[-1])
    assert field in values
    assert values[field] is None


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["error", "warning"])
async def test_logical_document_concrete_diagnostic_replaces_value(field):
    session = _ExecuteRecorder()
    repo = DBKnowledgeBaseRepository(session)

    await repo.update_document_status(
        "doc1",
        DocStatus.FAILED,
        **{field: "replacement"},
    )

    assert _compiled_values(session.statements[-1])[field] == "replacement"


def test_repository_protocols_share_one_importable_typed_unset():
    patch_module = importlib.import_module("app.domain.repositories.patch")
    unset = patch_module.UNSET
    assert type(unset) is patch_module.UnsetType

    from app.domain.repositories.knowledge_base_repository import (
        KnowledgeBaseRepository,
    )
    from app.domain.repositories.knowledge_version_repository import (
        KnowledgeVersionRepository,
    )

    logical = inspect.signature(KnowledgeBaseRepository.update_document_status)
    versioned = inspect.signature(KnowledgeVersionRepository.transition_document)
    for name in ("error", "warning"):
        assert logical.parameters[name].default is unset
        assert versioned.parameters[name].default is unset


class _ScalarResult:
    def __init__(self, value) -> None:
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _TransitionSession:
    def __init__(self, version, manifest, revision) -> None:
        self._records = (version, manifest, revision)
        self._index = 0

    async def execute(self, _statement):
        record = self._records[self._index % 3]
        self._index += 1
        return _ScalarResult(record)

    async def flush(self):
        return None


def _transition_fixture():
    version = SimpleNamespace(
        id="kbv1",
        knowledge_base_id="kb1",
        state=KnowledgeVersionState.BUILDING.value,
        published_at=None,
    )
    revision = SimpleNamespace(
        id="rev1",
        document_id="doc1",
        state=DocumentRevisionState.FAILED.value,
        parsed_blocks=[],
        page_count=0,
        error="old error",
        warning="old warning",
    )
    manifest = SimpleNamespace(
        version_id="kbv1",
        knowledge_base_id="kb1",
        document_id="doc1",
        document_revision_id="rev1",
        state=DocumentRevisionState.FAILED.value,
        error="old error",
        warning="old warning",
    )
    manifest.to_domain = lambda: manifest
    return version, manifest, revision


@pytest.mark.anyio
async def test_version_transition_omitted_diagnostics_preserve_manifest_and_revision():
    version, manifest, revision = _transition_fixture()
    repo = DBKnowledgeVersionRepository(_TransitionSession(version, manifest, revision))

    await repo.transition_document(
        "kbv1",
        "doc1",
        knowledge_base_id="kb1",
        state=DocumentRevisionState.FAILED,
    )

    assert (manifest.error, manifest.warning) == (
        "old error",
        "old warning",
    )
    assert (revision.error, revision.warning) == (
        "old error",
        "old warning",
    )


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["error", "warning"])
async def test_version_transition_explicit_none_clears_only_requested_field(
    field,
):
    patch_module = importlib.import_module("app.domain.repositories.patch")
    version, manifest, revision = _transition_fixture()
    repo = DBKnowledgeVersionRepository(_TransitionSession(version, manifest, revision))
    kwargs = {"error": patch_module.UNSET, "warning": patch_module.UNSET}
    kwargs[field] = None

    await repo.transition_document(
        "kbv1",
        "doc1",
        knowledge_base_id="kb1",
        state=DocumentRevisionState.FAILED,
        **kwargs,
    )

    other = "warning" if field == "error" else "error"
    assert getattr(manifest, field) is None
    assert getattr(revision, field) is None
    assert getattr(manifest, other) == f"old {other}"
    assert getattr(revision, other) == f"old {other}"


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["error", "warning"])
async def test_version_transition_replaces_only_requested_field(field):
    patch_module = importlib.import_module("app.domain.repositories.patch")
    version, manifest, revision = _transition_fixture()
    repo = DBKnowledgeVersionRepository(_TransitionSession(version, manifest, revision))
    kwargs = {"error": patch_module.UNSET, "warning": patch_module.UNSET}
    kwargs[field] = "replacement"

    await repo.transition_document(
        "kbv1",
        "doc1",
        knowledge_base_id="kb1",
        state=DocumentRevisionState.FAILED,
        **kwargs,
    )

    other = "warning" if field == "error" else "error"
    assert getattr(manifest, field) == "replacement"
    assert getattr(revision, field) == "replacement"
    assert getattr(manifest, other) == f"old {other}"
    assert getattr(revision, other) == f"old {other}"


@pytest.mark.anyio
async def test_failed_retry_clears_stale_diagnostics_through_indexed_success():
    version, manifest, revision = _transition_fixture()
    repo = DBKnowledgeVersionRepository(_TransitionSession(version, manifest, revision))

    await repo.transition_document(
        "kbv1",
        "doc1",
        knowledge_base_id="kb1",
        state=DocumentRevisionState.PARSING,
        error=None,
        warning=None,
    )
    await repo.transition_document(
        "kbv1",
        "doc1",
        knowledge_base_id="kb1",
        state=DocumentRevisionState.PARSED,
        parsed_blocks=[{"text": "recovered"}],
        page_count=1,
    )
    await repo.transition_document(
        "kbv1",
        "doc1",
        knowledge_base_id="kb1",
        state=DocumentRevisionState.INDEXING,
    )
    await repo.transition_document(
        "kbv1",
        "doc1",
        knowledge_base_id="kb1",
        state=DocumentRevisionState.INDEXED,
    )

    assert manifest.state == DocumentRevisionState.INDEXED.value
    assert revision.state == DocumentRevisionState.INDEXED.value
    assert (manifest.error, manifest.warning) == (None, None)
    assert (revision.error, revision.warning) == (None, None)


def test_ingestion_transition_wrapper_preserves_omitted_patch_fields():
    patch_module = importlib.import_module("app.domain.repositories.patch")
    from app.domain.services.knowledge_base.ingestion_runner import (
        KBIngestionRunner,
    )

    signature = inspect.signature(KBIngestionRunner._transition)
    for name in ("parsed_blocks", "page_count", "error", "warning"):
        assert signature.parameters[name].default is patch_module.UNSET
