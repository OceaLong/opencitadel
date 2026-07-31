#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Executable cross-layer acceptance invariants for versioned codebase analysis."""
from __future__ import annotations

import io
import stat
import zipfile
from unittest.mock import AsyncMock, MagicMock

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.application.errors.exceptions import BadRequestError
from app.application.services.codebase_service import CodebaseService
from app.domain.models.codebase import ArtifactKind, EdgeKind
from app.domain.models.codebase_version import CodebaseVersionState
from app.domain.models.scope import OwnerScope
from app.domain.services.codebase.artifact_generator import ArtifactGenerator
from app.domain.services.codebase.ingestion_runner import CodebaseIngestionRunner
from app.domain.services.codebase.hybrid_retriever import HybridCodeRetriever
from app.domain.services.codebase.source_validator import (
    CodebaseSourceValidator,
    normalize_contained_path,
)
from app.domain.services.codebase.static_analyzer import StaticAnalyzer
from tests.app.application.services.test_codebase_bound_source import (
    _service_fixture as _bound_source_fixture,
)
from tests.app.application.services.test_codebase_reanalysis_service import (
    _CodebaseRepo as _ReanalysisCodebaseRepo,
    _TaskState,
    _Uow as _ReanalysisUow,
    _VersionService,
    _plan,
)
from tests.app.domain.services.codebase.test_hybrid_retriever import (
    _Repo as _HybridRepo,
    _Uow as _HybridUow,
    _Vector,
    _chunk,
)
from tests.app.domain.services.codebase.test_versioned_ingestion_runner import (
    _BuildRepo as _FailureBuildRepo,
    _CodebaseRepo as _FailureCodebaseRepo,
    _Uow as _FailureUow,
    _VersionRepo as _FailureVersionRepo,
)


def _zip_bytes(entries: dict[str, bytes | str], *, symlink: bool = False) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            if symlink:
                info = zipfile.ZipInfo(name)
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, content)
            else:
                archive.writestr(name, content)
    return stream.getvalue()


def test_source_boundary_rejects_malicious_git_zip_and_paths():
    validator = CodebaseSourceValidator(
        resolver=lambda _host, _port: ["93.184.216.34"]
    )

    for url in (
        "file:///etc/passwd",
        "ssh://git@example.com/repo",
        "https://user:pass@example.com/repo.git",
        "https://127.0.0.1/repo.git",
        "https://169.254.169.254/latest/meta-data",
    ):
        with pytest.raises(BadRequestError):
            validator.validate_git_url(url)

    private_resolver = CodebaseSourceValidator(
        resolver=lambda _host, _port: ["93.184.216.34", "10.0.0.2"]
    )
    with pytest.raises(BadRequestError):
        private_resolver.validate_git_url("https://example.com/repo.git")

    for path in ("../secret", "src/../../secret", "/etc/passwd"):
        with pytest.raises(BadRequestError):
            normalize_contained_path("/workspace/codebase", path)

    for entries in (
        {"/abs.py": "x"},
        {"../escape.py": "x"},
        {"src/../../escape.py": "x"},
    ):
        with pytest.raises(BadRequestError):
            validator.validate_zip_bytes(_zip_bytes(entries))
    with pytest.raises(BadRequestError):
        validator.validate_zip_bytes(_zip_bytes({"link.py": "target.py"}, symlink=True))


@pytest.mark.asyncio
async def test_concurrent_reanalysis_is_idempotent_and_dispatches_once(
    monkeypatch,
):
    repo = _ReanalysisCodebaseRepo()
    version_service = _VersionService(
        [
            _plan(existing=False),
            _plan(existing=True),
        ]
    )
    service = CodebaseService(
        uow_factory=lambda: _ReanalysisUow(repo),
        sandbox_cls=MagicMock(),
        file_storage=object(),  # type: ignore[arg-type]
        codebase_version_service=version_service,  # type: ignore[arg-type]
    )
    task_state = _TaskState()
    service._task_state = task_state  # type: ignore[method-assign]
    dispatch = AsyncMock()
    monkeypatch.setattr(
        "app.application.services.codebase_service.RedisStreamTask.dispatch_to_worker",
        dispatch,
    )

    created = await service.reanalyze("cb1", scope=OwnerScope.personal("owner"))
    existing = await service.reanalyze("cb1", scope=OwnerScope.personal("owner"))

    assert created.ingest_task_id == "build-1"
    assert existing.ingest_task_id == "build-1"
    task_state.register_task.assert_awaited_once()
    dispatch.assert_awaited_once()
    assert len(version_service.calls) == 2


@pytest.mark.asyncio
async def test_core_build_failure_preserves_active_codebase(monkeypatch):
    codebase = _FailureCodebaseRepo()
    versions = _FailureVersionRepo()
    builds = _FailureBuildRepo()
    runner = CodebaseIngestionRunner(
        uow_factory=lambda: _FailureUow(codebase, versions, builds),
        sandbox_cls=MagicMock(),
        file_storage=MagicMock(),
    )
    monkeypatch.setattr(
        "app.domain.services.codebase.ingestion_runner.CodebaseIngestionRunner._materialize",
        AsyncMock(side_effect=RuntimeError("materialize failed")),
    )

    with pytest.raises(RuntimeError, match="materialize failed"):
        async for _event in runner.run_build("build-1"):
            pass

    assert codebase.codebase.active_version_id == "cbv1"
    assert versions.versions["cbv2"].state is CodebaseVersionState.FAILED


@pytest.mark.asyncio
async def test_old_session_reads_old_snapshot_after_new_publish():
    service, storage, codebase, version, _materialized, _uow = (
        await _bound_source_fixture()
    )
    codebase.active_version_id = "cbv2"

    content = await service.read_source(
        "cb1",
        "src/main.py",
        codebase_version_id=version.id,
        object_storage=storage,
    )

    assert "line one" in content
    assert storage.get_keys == [version.source_snapshot_key]


@pytest.mark.asyncio
async def test_vector_outage_keeps_lexical_search_and_marks_degraded():
    repo = _HybridRepo()
    repo.search_lexical.return_value = [(_chunk(), 0.9)]
    vector = _Vector()
    vector.embed.side_effect = TimeoutError("embedding unavailable")
    retriever = HybridCodeRetriever(lambda: _HybridUow(repo), vector_service=vector)

    response = await retriever.retrieve("cb1", "cbv1", "create user", limit=5)

    assert response.items
    assert response.items[0].sources == ("lexical",)
    assert response.capabilities["lexical_search"] is True
    assert response.capabilities["vector_search"] is False
    assert response.degraded_reasons == ["EMBEDDING_UNAVAILABLE"]


def test_symbols_ambiguous_calls_and_artifact_facts_have_evidence():
    analysis = StaticAnalyzer().analyze(
        files={
            "a.py": "def run():\n    return 1\n",
            "b.py": "def run():\n    return 2\n",
            "caller.py": "def caller():\n    return run()\n",
        },
        version_id="cbv1",
    )
    run_symbols = [symbol for symbol in analysis.symbols if symbol.name == "run"]
    call_edges = [
        edge
        for edge in analysis.edges
        if edge.kind is EdgeKind.CALL and edge.callee_name == "run"
    ]

    assert {symbol.qualified_name for symbol in run_symbols} == {
        "a.run",
        "b.run",
    }
    assert len(call_edges) == 1
    assert call_edges[0].resolution == "ambiguous"
    assert call_edges[0].dst_symbol_id is None
    assert call_edges[0].evidence

    result = ArtifactGenerator().generate_all_from_analysis(analysis)
    kinds = {artifact.kind for artifact in result.artifacts}
    assert ArtifactKind.FLOWCHART not in kinds
    assert ArtifactKind.DATA_FLOW not in kinds
    for artifact in result.artifacts:
        for edge in artifact.meta.get("edges", []):
            assert edge["evidence_refs"]


def test_alembic_has_only_e9_codebase_head_and_d8_is_its_parent():
    script = ScriptDirectory.from_config(Config("alembic.ini"))

    assert script.get_heads() == ["e9f0a1b2c3d4"]
    assert script.get_revision("e9f0a1b2c3d4").down_revision == (
        "d8e9f0a1b2c3"
    )
