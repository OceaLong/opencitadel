"""The project starts from one complete schema, without upgrade bridges."""

from itertools import pairwise
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.infrastructure.models.registry import MODEL_MODULES, model_metadata
from app.infrastructure.security import tenant_rls
from app.infrastructure.security.tenant_rls import (
    CHILD_TABLES,
    EXECUTION_ROOT_TABLES,
    PRIVATE_ROOT_TABLES,
    VISIBILITY_ROOT_TABLES,
)


def test_greenfield_schema_is_the_single_root() -> None:
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = script.get_revision("0001greenfield")

    assert revision is not None
    assert revision.down_revision is None
    assert script.get_bases() == ["0001greenfield"]
    assert script.get_heads() == ["0030evaluation_judge_history"]
    chain = list(script.walk_revisions())
    assert chain[-1].revision == "0001greenfield"
    assert all(newer.down_revision == older.revision for newer, older in pairwise(chain))
    versions = sorted(path.name for path in Path("alembic/versions").glob("*.py"))
    assert versions == [
        "0001greenfield_initial.py",
        "0002artifact_provenance.py",
        "0002execution_view.py",
        "0002view_queries.py",
        "0003resource_pins.py",
        "0004execution_configuration.py",
        "0005evaluation_datasets.py",
        "0006evaluation_configuration.py",
        "0007evaluation_recordings.py",
        "0008evaluation_environments.py",
        "0009evaluation_budget.py",
        "0010evaluation_batches.py",
        "0011evaluation_scores.py",
        "0012evaluation_judges.py",
        "0013evaluation_reviews.py",
        "0014evaluation_summary.py",
        "0015evaluation_archives.py",
        "0016execution_analysis.py",
        "0017execution_comparisons.py",
        "0018analysis_preferences.py",
        "0019analysis_chart_facts.py",
        "0020execution_exports.py",
        "0021analysis_native_reads.py",
        "0022analysis_point_facts.py",
        "0023analysis_runtime_repairs.py",
        "0024analysis_sealed_cache.py",
        "0025analysis_point_redaction.py",
        "0026analysis_redacted_inputs.py",
        "0027analysis_point_redaction.py",
        "0028analysis_scoped_jit.py",
        "0029analysis_team_sessions.py",
        "0030evaluation_judge_history.py",
    ]


def test_registry_explicitly_loads_execution_kernel_models() -> None:
    assert "app.infrastructure.execution.models" in MODEL_MODULES
    assert "app.infrastructure.models.runtime_policy" in MODEL_MODULES
    assert "app.infrastructure.models.app_config" not in MODEL_MODULES


def test_current_metadata_contains_only_current_execution_authorities() -> None:
    tables = model_metadata.tables

    assert {
        "execution_events",
        "execution_command_inbox",
        "execution_activity_tasks",
        "execution_scheduled_commands",
        "execution_run_projection",
        "execution_public_events",
        "knowledge_base_versions",
        "session_resource_bindings",
    } <= set(tables)
    assert {
        "codebases",
        "codebase_versions",
        "codebase_files",
        "codebase_symbols",
        "codebase_edges",
        "codebase_chunks",
        "codebase_artifacts",
        "session_events",
        "session_checkpoints",
        "resource_builds",
        "resource_build_events",
        "tool_approval_batches",
        "tool_approval_calls",
    }.isdisjoint(tables)
    assert {"task_id", "pending_phase"}.isdisjoint(tables["sessions"].c)
    assert {
        "inference_endpoints",
        "inference_models",
        "inference_bindings",
    } <= set(tables)
    assert {"llm_endpoints", "llm_models", "llm_model_preferences"}.isdisjoint(tables)
    assert {"provider", "base_url", "credential", "api_key"}.isdisjoint(
        tables["inference_models"].c
    )


def test_rls_catalog_references_only_current_metadata() -> None:
    catalog = (
        set(PRIVATE_ROOT_TABLES)
        | set(tenant_rls.EXECUTION_VIEW_TABLES)
        | set(VISIBILITY_ROOT_TABLES)
        | set(tenant_rls.POLICY_ROOT_TABLES)
        | set(EXECUTION_ROOT_TABLES)
        | set(CHILD_TABLES)
        | {
            "inference_bindings",
            "notifications",
            "service_api_keys",
            "user_quotas",
        }
    )

    assert catalog <= set(model_metadata.tables)
    assert {
        "session_resource_bindings",
        "knowledge_base_versions",
        "knowledge_base_version_documents",
        "knowledge_document_revisions",
        "knowledge_entity_refs",
    } <= set(CHILD_TABLES)


def test_runtime_policy_tables_replace_app_config_tables() -> None:
    tables = model_metadata.tables

    assert {
        "execution_policy_revisions",
        "operations_policy_revisions",
        "runtime_policy_heads",
    } <= set(tables)
    assert {"app_configs", "app_config_revisions"}.isdisjoint(tables)
    assert tables["execution_policy_revisions"].c.digest.unique is not True
    assert tables["operations_policy_revisions"].c.digest.unique is not True
    assert tables["runtime_policy_heads"].c.id.primary_key is True
    assert tables["runtime_policy_heads"].c.version.nullable is False


def test_vector_and_keyword_storage_are_first_class_schema_columns() -> None:
    knowledge_chunks = model_metadata.tables["knowledge_chunks"]
    memory_entries = model_metadata.tables["memory_entries"]

    assert {"content_tsv", "embedding"} <= set(knowledge_chunks.c.keys())
    assert "embedding" in memory_entries.c
    assert {index.name for index in knowledge_chunks.indexes} >= {
        "ix_kb_chunks_embedding",
        "ix_kb_chunks_tsv",
    }
    assert "ix_memory_entries_embedding_hnsw" in {index.name for index in memory_entries.indexes}


def test_patrol_runs_require_a_formal_execution_identity() -> None:
    patrol_runs = model_metadata.tables["patrol_runs"]

    assert patrol_runs.c.execution_run_id.nullable is False


def test_deleted_patrol_packs_do_not_pin_integration_lifecycle() -> None:
    patrol_packs = model_metadata.tables["patrol_packs"]

    assert patrol_packs.c.mcp_server_id.nullable is False
    assert patrol_packs.c.mcp_server_id.foreign_keys == set()
    assert patrol_packs.c.validation_run_id.nullable is True


def test_session_timestamps_are_timezone_aware() -> None:
    sessions = model_metadata.tables["sessions"]

    for column_name in ("latest_message_at", "created_at", "updated_at"):
        assert sessions.c[column_name].type.timezone is True


def test_new_credentials_have_only_current_storage_markers() -> None:
    endpoints = model_metadata.tables["inference_endpoints"]
    mcp_servers = model_metadata.tables["mcp_servers"]

    assert str(endpoints.c.credential_encryption.server_default.arg) == "'fernet_v2'"
    for column in ("url_encryption", "headers_encryption", "env_encryption"):
        assert str(mcp_servers.c[column].server_default.arg) == "'plaintext'"


def test_export_forward_tables_are_not_pulled_into_initial_create_all():
    assert not (
        {
            "execution_exports",
            "export_staging",
            "export_quota_serializers",
            "export_resources",
            "export_rows",
            "export_receipts",
            "export_source_facts",
            "export_object_intents",
            "export_download_uses",
        }
        & set(model_metadata.tables)
    )
