# ruff: noqa: F811 -- imported fixture
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

from alembic import command
from tests.app.alembic.test_evaluation_datasets_migration import migration_database  # noqa: F401
from tests.app.alembic.test_execution_view_migration import _configure_migration


def migration():
    spec = importlib.util.spec_from_file_location(
        "e02_test_migration", Path("alembic/versions/0006evaluation_configuration.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("precreated", [False, True])
def test_forward_and_exact_shape_preserve_immutable_and_scope_guards(
    migration_database, precreated
):
    engine, config = migration_database
    command.upgrade(config, "0005evaluation_datasets")
    module = migration()
    if precreated:
        with engine.begin() as db:
            for name, ddl in module.TABLES.items():
                db.execute(sa.text(f"CREATE TABLE {name} ({ddl})"))
            for statement in module.INDEXES:
                db.execute(sa.text(statement))
    command.upgrade(config, "0006evaluation_configuration")
    with engine.begin() as db:
        assert (
            db.scalar(sa.text("SELECT version_num FROM alembic_version"))
            == "0006evaluation_configuration"
        )
        for name in module.TABLES:
            assert db.scalar(
                sa.text("SELECT relforcerowsecurity FROM pg_class WHERE relname=:name"),
                {"name": name},
            )
        assert (
            db.scalar(
                sa.text(
                    "SELECT count(*) FROM pg_constraint WHERE conrelid='evaluation_suite_versions'::regclass AND contype='f'"
                )
            )
            == 3
        )


def test_fresh_migration_and_shape_drift_rejected(migration_database):
    engine, config = migration_database
    command.upgrade(config, "0006evaluation_configuration")
    module = migration()
    with engine.begin() as db:
        _configure_migration(db)
        module.upgrade_connection(db)
    with engine.begin() as db:
        db.execute(sa.text("ALTER TABLE evaluation_preflights ALTER COLUMN body DROP NOT NULL"))
    with engine.begin() as db:
        _configure_migration(db)
        with pytest.raises(RuntimeError, match="schema mismatch"):
            module.upgrade_connection(db)
