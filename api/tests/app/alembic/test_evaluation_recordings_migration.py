# ruff: noqa: F811
import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

from alembic import command
from tests.app.alembic.test_evaluation_datasets_migration import migration_database  # noqa: F401
from tests.app.alembic.test_execution_view_migration import _configure_migration


def migration():
    spec = importlib.util.spec_from_file_location(
        "e03_test_migration", Path("alembic/versions/0007evaluation_recordings.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("precreated", [False, True])
def test_forward_and_exact_precreated(migration_database, precreated):
    engine, config = migration_database
    command.upgrade(config, "0006evaluation_configuration")
    module = migration()
    if precreated:
        with engine.begin() as db:
            for name, ddl in module.TABLES.items():
                db.execute(sa.text(f"CREATE TABLE {name} ({ddl})"))
            for statement in module.INDEXES:
                db.execute(sa.text(statement))
    command.upgrade(config, "0007evaluation_recordings")
    with engine.begin() as db:
        assert (
            db.scalar(sa.text("SELECT version_num FROM alembic_version"))
            == "0007evaluation_recordings"
        )
        for name in module.TABLES:
            assert db.scalar(
                sa.text("SELECT relforcerowsecurity FROM pg_class WHERE relname=:name"),
                {"name": name},
            )
        assert (
            db.scalar(
                sa.text(
                    "SELECT count(*) FROM pg_constraint WHERE conrelid='evaluation_replay_ledger'::regclass AND contype='u'"
                )
            )
            == 1
        )


def test_fresh_and_drift(migration_database):
    engine, config = migration_database
    command.upgrade(config, "0007evaluation_recordings")
    module = migration()
    with engine.begin() as db:
        _configure_migration(db)
        module.upgrade_connection(db)
    with engine.begin() as db:
        db.execute(
            sa.text("ALTER TABLE evaluation_recording_slots ALTER COLUMN match_key DROP NOT NULL")
        )
    with engine.begin() as db:
        _configure_migration(db)
        with pytest.raises(RuntimeError, match="schema mismatch"):
            module.upgrade_connection(db)
