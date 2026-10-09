import importlib.util
import os
from contextlib import suppress
from pathlib import Path

import pytest
import sqlalchemy as sa

from alembic import command
from tests.app.alembic.test_execution_view_migration import (
    _configure_migration,
    isolated_database,
)


@pytest.fixture
def migration_database(_postgres_available, monkeypatch):
    """Run DDL as the existing non-superuser migration login on its owned DB."""
    from core.config import load_deployment_settings, sqlalchemy_sync_migration_database_uri

    settings = load_deployment_settings()
    url = sa.engine.make_url(sqlalchemy_sync_migration_database_uri(settings)).set(
        username=os.environ["POSTGRES_MIGRATION_USER"],
        password=os.environ["POSTGRES_MIGRATION_PASSWORD"],
    )
    replacement = settings.model_copy(
        update={"sqlalchemy_migration_database_uri": url.render_as_string(hide_password=False)}
    )
    monkeypatch.setattr(
        "tests.app.alembic.test_execution_view_migration.load_deployment_settings",
        lambda: replacement,
    )
    generator = isolated_database.__wrapped__(_postgres_available)
    engine, config = next(generator)
    try:
        with engine.connect() as db:
            assert db.scalar(
                sa.text(
                    "SELECT NOT rolsuper AND NOT rolbypassrls FROM pg_roles WHERE rolname=current_user"
                )
            )
            assert db.scalar(
                sa.text(
                    "SELECT datdba=(SELECT oid FROM pg_roles WHERE rolname=current_user) FROM pg_database WHERE datname=current_database()"
                )
            )
        yield engine, config
    finally:
        with suppress(StopIteration):
            next(generator)


def migration():
    spec = importlib.util.spec_from_file_location(
        "e01_test_migration", Path("alembic/versions/0005evaluation_datasets.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("precreated", [False, True])
def test_forward_old_and_precreated_exact_new_shape(migration_database, precreated):
    engine, config = migration_database
    command.upgrade(config, "0004execution_configuration")
    module = migration()
    if precreated:
        with engine.begin() as db:
            for name, ddl in module.TABLES.items():
                db.execute(sa.text(f"CREATE TABLE {name} ({ddl})"))
            for sql in module.INDEXES:
                db.execute(sa.text(sql))
    command.upgrade(config, "0005evaluation_datasets")
    with engine.begin() as db:
        assert (
            db.scalar(sa.text("SELECT version_num FROM alembic_version"))
            == "0005evaluation_datasets"
        )
        for name in module.TABLES:
            assert db.scalar(
                sa.text("SELECT relforcerowsecurity FROM pg_class WHERE relname=:name"),
                {"name": name},
            )


def test_fresh_greenfield_and_existing_shape_drift_fail_closed(migration_database):
    engine, config = migration_database
    command.upgrade(config, "0005evaluation_datasets")
    module = migration()
    with engine.begin() as db:
        _configure_migration(db)
        module.upgrade_connection(db)
    with engine.begin() as db:
        db.execute(
            sa.text("ALTER TABLE evaluation_case_revisions ALTER COLUMN object_index DROP NOT NULL")
        )
    with engine.begin() as db:
        _configure_migration(db)
        with pytest.raises(RuntimeError, match="schema mismatch"):
            module.upgrade_connection(db)
