import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

from alembic import command
from tests.app.alembic.test_execution_view_migration import (  # noqa: F401
    _configure_migration,
    isolated_database,
)


def test_forward_provenance_migration_preserves_and_validates(isolated_database):  # noqa: F811
    engine, config = isolated_database
    path = Path("alembic/versions/0002artifact_provenance.py")
    assert path.exists(), "private production receipt migration missing"
    command.upgrade(config, "0002view_queries")
    command.upgrade(config, "head")
    spec = importlib.util.spec_from_file_location("f05_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with engine.begin() as conn:
        _configure_migration(conn)
        module.upgrade_connection(conn)
        # The immediate pre-review shape had invocation but no retry state.
        for name in module.RECONCILIATION_COLUMNS:
            conn.execute(sa.text(f"ALTER TABLE artifact_production_receipts DROP COLUMN {name}"))
        module.upgrade_connection(conn)
        # The initial F05 shape also had no optional invocation.
        for name in module.RECONCILIATION_COLUMNS:
            conn.execute(sa.text(f"ALTER TABLE artifact_production_receipts DROP COLUMN {name}"))
        conn.execute(sa.text("ALTER TABLE artifact_production_receipts DROP COLUMN invocation_id"))
        module.upgrade_connection(conn)
        assert "invocation_id" in {
            column["name"]
            for column in sa.inspect(conn).get_columns("artifact_production_receipts")
        }
        api = conn.scalar(sa.text("SELECT current_setting('app.runtime_database_role')"))
        for name in ("artifact_production_receipts", "artifact_upload_intents"):
            assert conn.scalar(
                sa.text("SELECT has_table_privilege(:role,:name,'INSERT')"),
                {"role": api, "name": name},
            )
            assert not conn.scalar(
                sa.text("SELECT has_table_privilege(:role,:name,'SELECT')"),
                {"role": api, "name": name},
            )
    with engine.begin() as conn:
        _configure_migration(conn)
        conn.execute(
            sa.text(
                "ALTER TABLE artifact_production_receipts DROP CONSTRAINT artifact_production_receipts_pkey"
            )
        )
        with pytest.raises(RuntimeError, match="schema mismatch"):
            module.upgrade_connection(conn)
