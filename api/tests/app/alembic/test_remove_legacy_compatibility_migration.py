"""Contract tests for retiring the completed compatibility window."""
import importlib.util
from io import StringIO
from pathlib import Path

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory


PATH = (
    Path(__file__).parents[3]
    / "alembic"
    / "versions"
    / "aa06contract_remove_legacy_compatibility.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("legacy_contract", PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _offline_sql(action: str) -> str:
    module = _load_migration()
    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )
    previous = module.op
    module.op = Operations(context)
    try:
        getattr(module, action)()
    finally:
        module.op = previous
    return output.getvalue()


def test_contract_cleanup_is_the_single_head_after_patrol_remediations():
    script = ScriptDirectory.from_config(Config("alembic.ini"))

    assert script.get_heads() == ["aa06contract"]
    assert (
        script.get_revision("aa06contract").down_revision
        == "94feff5b0d54"
    )


def test_upgrade_backfills_and_validates_before_dropping_compatibility():
    sql = _offline_sql("upgrade")

    assert "INSERT INTO session_resource_bindings" in sql
    assert "unresolved codebase session bindings" in sql
    assert "unresolved knowledge-base session bindings" in sql
    assert "pending_metadata - 'pending_tool_call'" in sql
    assert "pending_phase = NULL" in sql
    assert "ALTER COLUMN version_id SET NOT NULL" in sql
    assert "VALIDATE CONSTRAINT fk_knowledge_chunks_version_owner" in sql
    assert "DROP COLUMN codebase_id" in sql
    assert "DROP COLUMN knowledge_base_id" in sql
    assert "DROP COLUMN legacy_v1_migrated" in sql
    assert "DROP COLUMN legacy_snapshot" in sql


def test_downgrade_restores_schema_without_recreating_single_call_metadata():
    sql = _offline_sql("downgrade")

    assert "ADD COLUMN codebase_id" in sql
    assert "ADD COLUMN knowledge_base_id" in sql
    assert "ADD COLUMN legacy_v1_migrated" in sql
    assert "ADD COLUMN legacy_snapshot" in sql
    assert "pending_tool_call" not in sql
