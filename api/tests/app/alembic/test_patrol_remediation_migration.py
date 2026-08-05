#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Contract tests for the patrol_remediations table migration."""
import importlib.util
from io import StringIO
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations


MIGRATION_PATH = (
    Path(__file__).parents[3]
    / "alembic"
    / "versions"
    / "94feff5b0d54_create_patrol_remediations.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("patrol_remediation_migration", MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgrade_sql() -> str:
    migration = _load_migration()
    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )
    previous_op = migration.op
    migration.op = Operations(context)
    try:
        migration.upgrade()
    finally:
        migration.op = previous_op
    return output.getvalue()


def _downgrade_sql() -> str:
    migration = _load_migration()
    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )
    previous_op = migration.op
    migration.op = Operations(context)
    try:
        migration.downgrade()
    finally:
        migration.op = previous_op
    return output.getvalue()


def test_migration_is_the_single_head_after_patrol_scheduled_job_binding():
    migration = _load_migration()

    assert migration.revision == "94feff5b0d54"
    assert migration.down_revision == "f1a2b3c4d5e6"


def test_upgrade_creates_patrol_remediations_table_with_foreign_keys():
    sql = _upgrade_sql()

    assert "CREATE TABLE patrol_remediations" in sql
    for statement in (
        "id VARCHAR(36) NOT NULL",
        "pack_id VARCHAR(36) NOT NULL",
        "run_id VARCHAR(36) NOT NULL",
        "finding_id VARCHAR(36) NOT NULL",
        "check_result_id VARCHAR(36) NOT NULL",
        "fingerprint VARCHAR(64) NOT NULL",
        "session_id VARCHAR(255)",
        "action VARCHAR(32) NOT NULL",
        "target_namespace VARCHAR(255) NOT NULL",
        "target_workload VARCHAR(255) DEFAULT '' NOT NULL",
        "target_kind VARCHAR(64) DEFAULT 'Deployment' NOT NULL",
        "params JSONB DEFAULT '{}'::jsonb NOT NULL",
        "params_hash VARCHAR(64) NOT NULL",
        "idempotency_key VARCHAR(255) NOT NULL",
        "status VARCHAR(32) DEFAULT 'proposed' NOT NULL",
        "created_by VARCHAR(255) NOT NULL",
    ):
        assert statement in sql, statement

    assert "FOREIGN KEY(pack_id) REFERENCES patrol_packs (id) ON DELETE RESTRICT" in sql
    assert "FOREIGN KEY(run_id) REFERENCES patrol_runs (id) ON DELETE CASCADE" in sql
    assert "FOREIGN KEY(finding_id) REFERENCES patrol_findings (id) ON DELETE CASCADE" in sql
    assert "FOREIGN KEY(check_result_id) REFERENCES patrol_check_results (id) ON DELETE CASCADE" in sql
    assert "FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE SET NULL" in sql
    assert "FOREIGN KEY(recheck_run_id) REFERENCES patrol_runs (id) ON DELETE SET NULL" in sql
    assert "FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE RESTRICT" in sql
    assert "UNIQUE (idempotency_key)" in sql


def test_upgrade_enforces_single_active_remediation_per_finding():
    sql = _upgrade_sql()

    assert (
        "CREATE UNIQUE INDEX uq_patrol_remediations_active_finding "
        "ON patrol_remediations (finding_id) "
        "WHERE status NOT IN ('verified', 'failed', 'cancelled')" in sql
    )
    assert "CREATE INDEX ix_patrol_remediations_fingerprint ON patrol_remediations (fingerprint)" in sql
    assert "CREATE INDEX ix_patrol_remediations_run_id ON patrol_remediations (run_id)" in sql


def test_upgrade_enables_row_level_security_scoped_through_patrol_runs():
    sql = _upgrade_sql()

    assert "ALTER TABLE patrol_remediations ENABLE ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE patrol_remediations FORCE ROW LEVEL SECURITY" in sql
    assert "CREATE POLICY patrol_remediations_tenant_select ON patrol_remediations FOR SELECT" in sql
    assert "CREATE POLICY patrol_remediations_tenant_insert ON patrol_remediations FOR INSERT" in sql
    assert "CREATE POLICY patrol_remediations_tenant_update ON patrol_remediations FOR UPDATE" in sql
    assert "CREATE POLICY patrol_remediations_tenant_delete ON patrol_remediations FOR DELETE" in sql
    assert (
        "EXISTS (SELECT 1 FROM patrol_runs WHERE patrol_runs.id = patrol_remediations.run_id)" in sql
    )


def test_downgrade_disables_policies_and_drops_table():
    sql = _downgrade_sql()

    assert "DROP POLICY IF EXISTS patrol_remediations_tenant_select ON patrol_remediations" in sql
    assert "DROP POLICY IF EXISTS patrol_remediations_tenant_insert ON patrol_remediations" in sql
    assert "DROP POLICY IF EXISTS patrol_remediations_tenant_update ON patrol_remediations" in sql
    assert "DROP POLICY IF EXISTS patrol_remediations_tenant_delete ON patrol_remediations" in sql
    assert "ALTER TABLE patrol_remediations NO FORCE ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE patrol_remediations DISABLE ROW LEVEL SECURITY" in sql
    assert "DROP TABLE patrol_remediations" in sql
