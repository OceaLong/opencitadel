import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

from alembic import command
from tests.app.alembic.test_execution_view_migration import (  # noqa: F401
    _configure_migration,
    isolated_database,
)


def test_forward_configuration_schema(isolated_database):  # noqa: F811
    engine, config = isolated_database
    command.upgrade(config, "0004execution_configuration")
    with engine.begin() as db:
        names = set(sa.inspect(db).get_table_names())
        assert {
            "execution_configurations",
            "execution_model_dispatches",
            "execution_model_settlements",
            "execution_usage_publications",
            "execution_usage_delivery",
        } <= names
        for name in (
            "execution_configurations",
            "execution_model_dispatches",
            "execution_model_settlements",
            "execution_usage_publications",
            "execution_usage_delivery",
        ):
            assert db.scalar(
                sa.text("SELECT relforcerowsecurity FROM pg_class WHERE relname=:name"),
                {"name": name},
            )
        assert (
            db.scalar(sa.text("SELECT version_num FROM alembic_version"))
            == "0004execution_configuration"
        )


def test_precreated_exact_schema_and_drift(isolated_database):  # noqa: F811
    engine, config = isolated_database
    command.upgrade(config, "head")
    spec = importlib.util.spec_from_file_location(
        "f07_migration", Path("alembic/versions/0004execution_configuration.py")
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with engine.begin() as db:
        _configure_migration(db)
        migration.upgrade_connection(db)
    with engine.begin() as db:
        db.execute(
            sa.text("ALTER TABLE execution_model_dispatches ALTER COLUMN ordinal DROP NOT NULL")
        )
    with engine.begin() as db:
        _configure_migration(db)
        with pytest.raises(RuntimeError, match="schema mismatch"):
            migration.upgrade_connection(db)
