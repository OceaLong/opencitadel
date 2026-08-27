"""Architecture contracts for candidate artifacts backed by formal Runs."""

from pathlib import Path

from app.domain.repositories.uow import IUnitOfWork
from app.infrastructure.models.base import Base
from app.infrastructure.models.codebase_version import CodebaseVersionORM
from app.infrastructure.models.knowledge_version import KnowledgeBaseVersionORM

API_ROOT = Path(__file__).parents[3]
REPO_ROOT = API_ROOT.parent


def test_candidate_versions_are_the_only_persisted_artifact_build_state() -> None:
    tables = Base.metadata.tables

    assert "resource_builds" not in tables
    assert "resource_build_events" not in tables
    for model in (KnowledgeBaseVersionORM, CodebaseVersionORM):
        assert model.__table__.c.build_id.nullable is False
        assert model.__table__.c.request_key.nullable is False
        index_names = {index.name for index in model.__table__.indexes}
        assert any(name and name.endswith("_versions_build") for name in index_names)
        assert any(name and name.endswith("_versions_building_candidate") for name in index_names)


def test_retrieval_columns_and_indexes_are_part_of_current_metadata() -> None:
    knowledge_chunks = Base.metadata.tables["knowledge_chunks"]
    codebase_chunks = Base.metadata.tables["codebase_chunks"]

    assert {"content_tsv", "embedding"} <= set(knowledge_chunks.c.keys())
    assert "embedding" in codebase_chunks.c
    assert {index.name for index in knowledge_chunks.indexes} >= {
        "ix_kb_chunks_embedding",
        "ix_kb_chunks_tsv",
    }
    assert {index.name for index in codebase_chunks.indexes} >= {
        "ix_codebase_chunks_embedding",
    }


def test_unit_of_work_names_the_remaining_repository_by_its_real_capability() -> None:
    annotations = IUnitOfWork.__annotations__

    assert "resource_bindings" in annotations
    assert "resource_governance" not in annotations


def test_resource_pipelines_have_no_retired_build_lifecycle_or_import_bridge() -> None:
    paths = (
        API_ROOT / "app/domain/services/knowledge_base/ingestion_runner.py",
        API_ROOT / "app/domain/services/codebase/ingestion_runner.py",
        API_ROOT / "app/domain/services/knowledge_base/version_builder.py",
        API_ROOT / "app/domain/services/codebase/version_builder.py",
        API_ROOT / "app/infrastructure/repositories/kb/index_mixin.py",
    )
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)

    for retired in (
        "ResourceBuildService",
        "ResourceBuildEvent",
        "BuildState",
        "needs_chunk_clone",
        "clone_version_chunks",
        "legacy_entries",
    ):
        assert retired not in source


def test_greenfield_metadata_requires_candidate_identity() -> None:
    for table_name in ("knowledge_base_versions", "codebase_versions"):
        table = Base.metadata.tables[table_name]
        assert table.c.build_id.nullable is False
        assert table.c.request_key.nullable is False


def test_ui_build_contract_uses_formal_run_projection_only() -> None:
    sources = "\n".join(
        (REPO_ROOT / relative).read_text(encoding="utf-8")
        for relative in (
            "ui/src/lib/api/types/codebase.ts",
            "ui/src/lib/api/types/knowledge.ts",
            "ui/src/hooks/use-version-build-polling.ts",
            "ui/src/components/resource/build-candidate-panel.tsx",
        )
    )

    assert "status: ExecutionRunStatus" in sources
    for retired in (
        "BuildState",
        "command_key",
        "last_event_seq",
        "heartbeat_at",
        "error_message",
        "activeBuild.state",
    ):
        assert retired not in sources
