import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

from alembic import command
from tests.app.alembic.test_execution_view_migration import (  # noqa:F401
    _configure_migration,
    isolated_database,
)


def test_resource_pins_fresh_precreated_shape_and_drift(isolated_database):  # noqa: F811
    engine, config = isolated_database
    command.upgrade(config, "head")
    spec = importlib.util.spec_from_file_location(
        "f06_migration", Path("alembic/versions/0003resource_pins.py")
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.down_revision == "0002artifact_provenance"
    with engine.begin() as conn:
        _configure_migration(conn)
        migration.upgrade_connection(conn)
        api = conn.scalar(sa.text("SELECT current_setting('app.runtime_database_role')"))
        for table in ("execution_public_content", "execution_content_bindings"):
            assert conn.scalar(
                sa.text("SELECT has_table_privilege(:role,:table,'SELECT')"),
                {"role": api, "table": table},
            )
            for permission in ("INSERT", "UPDATE", "DELETE"):
                assert not conn.scalar(
                    sa.text("SELECT has_table_privilege(:role,:table,:permission)"),
                    {"role": api, "table": table, "permission": permission},
                )
        assert not conn.scalar(
            sa.text("SELECT has_table_privilege(:role,'artifact_production_receipts','SELECT')"),
            {"role": api},
        )
        for permission in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert not conn.scalar(
                sa.text("SELECT has_table_privilege(:role,'artifact_retired_objects',:permission)"),
                {"role": api, "permission": permission},
            )
        assert not conn.scalar(
            sa.text(
                "SELECT has_function_privilege(:role,'opencitadel_capture_retired_artifact()','EXECUTE')"
            ),
            {"role": api},
        )
    with engine.begin() as conn:
        _configure_migration(conn)
        conn.execute(
            sa.text("ALTER TABLE resource_pins ALTER COLUMN resource_version DROP NOT NULL")
        )
        with pytest.raises(RuntimeError, match="schema mismatch"):
            migration.upgrade_connection(conn)
