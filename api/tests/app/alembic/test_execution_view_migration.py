"""Additive execution view migration contract."""

import importlib.util
import os
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from alembic import command
from core.config import load_deployment_settings, sqlalchemy_sync_migration_database_uri


def test_execution_view_is_additive_revision():
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    assert scripts.get_bases() == ["0001greenfield"]
    assert scripts.get_revision("0002execution_view").down_revision == "0001greenfield"
    assert scripts.get_revision("0002view_queries").down_revision == "0002execution_view"
    assert scripts.get_revision("0002artifact_provenance").down_revision == "0002view_queries"
    assert scripts.get_revision("0003resource_pins").down_revision == "0002artifact_provenance"
    assert scripts.get_revision("0004execution_configuration").down_revision == "0003resource_pins"
    assert (
        scripts.get_revision("0005evaluation_datasets").down_revision
        == "0004execution_configuration"
    )
    assert scripts.get_heads() == ["0030evaluation_judge_history"]


def test_view_schema_has_scoped_unique_identities_and_no_sequence_order():
    from app.infrastructure.models.registry import model_metadata

    tables = model_metadata.tables
    names = {
        "execution_view_runs",
        "execution_view_steps",
        "execution_view_checkpoints",
        "artifact_version_provenance",
        "execution_usage_facts",
        "execution_view_observations",
    }
    assert names <= tables.keys()
    from sqlalchemy import CheckConstraint, UniqueConstraint

    for name in names:
        table = tables[name]
        assert {
            "owner_user_id",
            "team_id",
            "created_by",
            "created_at",
            "updated_at",
            "schema_version",
        } <= set(table.c.keys())
        assert any(
            isinstance(c, CheckConstraint) and c.name == f"ck_{name}_owner_scope"
            for c in table.constraints
        )
        assert table.c.created_at.type.timezone
    steps = tables["execution_view_steps"]
    uniques = {
        tuple(c.columns.keys()) for c in steps.constraints if isinstance(c, UniqueConstraint)
    }
    assert ("run_id", "step_id") in uniques
    assert ("run_id", "step_id", "attempt_id") in uniques
    journal = tables["execution_view_observations"]
    assert journal.c.observed_order.identity is None
    assert journal.c.observed_order.server_default is None
    assert {
        "formal_position",
        "progress_position",
        "observed_order",
        "observed_at",
        "projection_revision",
        "projector_version",
        "source_identity",
    } <= set(journal.c.keys())
    checkpoint = tables["execution_view_checkpoints"]
    assert {"formal_position", "progress_position", "observed_order"} <= set(checkpoint.c.keys())
    provenance = tables["artifact_version_provenance"]
    assert provenance.c.producer_run_id.nullable
    assert provenance.c.produced_event_id.nullable
    assert tables["execution_usage_facts"].c.cost_usd.nullable


@pytest.fixture
def isolated_database(_postgres_available):
    """Only create/drop a fresh random database owned by this test invocation."""
    settings = load_deployment_settings()
    url = sa.engine.make_url(sqlalchemy_sync_migration_database_uri(settings))
    admin_url = url.set(
        database="postgres",
        username=os.getenv("POSTGRES_ADMIN_USER") or url.username,
        password=os.getenv("POSTGRES_ADMIN_PASSWORD") or url.password,
    )
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    name = "test_execution_view_" + uuid4().hex
    with admin.connect() as connection:
        connection.execute(sa.text(f'CREATE DATABASE "{name}" OWNER "{url.username}"'))
    engine = sa.create_engine(url.set(database=name))
    config = Config("alembic.ini")
    config.attributes["deployment_settings"] = settings.model_copy(
        update={
            "sqlalchemy_migration_database_uri": url.set(database=name).render_as_string(
                hide_password=False
            )
        }
    )
    try:
        # Non-trusted extensions require bootstrap authority, as in deployment.
        with sa.create_engine(
            admin_url.set(database=name), poolclass=sa.pool.NullPool
        ).begin() as connection:
            for extension in ("vector", "pgcrypto", "uuid-ossp", "pg_trgm"):
                connection.execute(sa.text(f'CREATE EXTENSION IF NOT EXISTS "{extension}"'))
        yield engine, config
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE "{name}"'))
        admin.dispose()


def test_empty_database_to_frozen_view_revision_and_existing_shape_validation(isolated_database):
    engine, config = isolated_database
    command.upgrade(config, "0002execution_view")
    from app.infrastructure.migrations.execution_view_ddl import upgrade_execution_view

    with engine.begin() as connection:
        assert (
            connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
            == "0002execution_view"
        )
        _configure_migration(connection)
        upgrade_execution_view(connection)


def _configure_migration(connection):
    from app.infrastructure.security.db_authorization import configure_sync_system_authorization

    settings = load_deployment_settings()
    for key, value in [
        ("app.runtime_database_role", "opencitadel_execution_api"),
        ("app.execution_runtime_role", "opencitadel_execution_kernel"),
        ("app.rls_signing_secret", settings.database_authorization_signing_secret),
    ]:
        connection.execute(
            sa.text("SELECT set_config(:key, :value, true)"), {"key": key, "value": value}
        )
    configure_sync_system_authorization(
        connection,
        actor="f01-migration-test",
        signing_secret=settings.database_authorization_signing_secret,
    )


def test_legacy_head_upgrade_preserves_persisted_events(isolated_database):
    engine, config = isolated_database
    # Execute the unchanged historical revision with its old table set, without
    # the current registry's new read tables (fresh greenfield takes the other path).
    from app.infrastructure.models.registry import model_metadata
    from app.infrastructure.security.tenant_rls import EXECUTION_VIEW_TABLES

    spec = importlib.util.spec_from_file_location(
        "legacy_greenfield", Path("alembic/versions/0001greenfield_initial.py")
    )
    legacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy)
    excluded = EXECUTION_VIEW_TABLES | {
        "resource_pins",
        "execution_public_content",
        "execution_content_bindings",
        "artifact_retired_objects",
    }
    old_metadata = sa.MetaData()
    for table in model_metadata.sorted_tables:
        if table.name not in excluded:
            cloned = table.to_metadata(old_metadata)
            if table.name == "files":
                for constraint in list(cloned.constraints):
                    if constraint.name == "ck_files_content_identity":
                        cloned.constraints.remove(constraint)
                for column in ("content_digest", "object_identity", "content_available"):
                    cloned._columns.remove(cloned.c[column])
    legacy.model_metadata = old_metadata
    apply_rls = legacy.apply_row_level_security
    legacy.apply_row_level_security = lambda execute: apply_rls(
        lambda statements: execute(
            [s for s in statements if not any(name in s for name in excluded)]
        )
    )
    with engine.begin() as connection:
        _configure_migration(connection)
        connection.execute(
            sa.text("CREATE TABLE alembic_version (version_num varchar(32) NOT NULL PRIMARY KEY)")
        )
        with Operations.context(MigrationContext.configure(connection)):
            legacy.upgrade()
        connection.execute(
            sa.text(
                "INSERT INTO execution_stream_owners (stream_type, stream_id, owner_user_id) VALUES ('run', 'legacy-run', 'legacy-owner')"
            )
        )
        from datetime import UTC, datetime

        from app.domain.execution.events import StoredEvent
        from app.domain.execution.store import calculate_event_hash, verify_event_hashes
        from app.infrastructure.execution.models import ExecutionEventORM

        stored = StoredEvent(
            position=1,
            event_id=uuid4(),
            stream_type="run",
            stream_id="legacy-run",
            stream_version=1,
            event_type="RunStarted",
            event_schema_version=1,
            public_payload={},
            internal_payload={},
            secret_ref=None,
            owner_user_id="legacy-owner",
            team_id=None,
            correlation_id=uuid4(),
            causation_id=None,
            occurred_at=datetime(2026, 9, 7, tzinfo=UTC),
            prev_hash="0" * 64,
            event_hash="0" * 64,
        )
        stored = stored.model_copy(update={"event_hash": calculate_event_hash(stored)})
        verify_event_hashes([stored])
        connection.execute(ExecutionEventORM.__table__.insert(), stored.model_dump())
        before = connection.execute(
            sa.text("SELECT row_to_json(e)::text FROM execution_events e")
        ).scalar_one()
        assert not sa.inspect(connection).has_table("execution_view_runs")
    command.stamp(config, "0001greenfield")
    command.upgrade(config, "head")
    with engine.begin() as connection:
        _configure_migration(connection)
        assert (
            connection.execute(
                sa.text("SELECT row_to_json(e)::text FROM execution_events e")
            ).scalar_one()
            == before
        )
        assert sa.inspect(connection).has_table("execution_view_observations")


@pytest.mark.parametrize(
    "mutation",
    [
        "ALTER TABLE execution_view_steps DROP COLUMN semantic_key",
        "ALTER TABLE execution_view_steps ALTER COLUMN status TYPE varchar(12)",
        "ALTER TABLE execution_view_steps ALTER COLUMN status DROP NOT NULL",
        "ALTER TABLE execution_view_steps ALTER COLUMN schema_version SET DEFAULT 2",
        "ALTER TABLE execution_view_steps DROP CONSTRAINT ck_execution_view_steps_owner_scope",
        "ALTER TABLE execution_view_steps DROP CONSTRAINT uq_execution_view_steps_step",
        (
            "ALTER TABLE execution_view_steps DROP CONSTRAINT uq_execution_view_steps_attempt; "
            "ALTER TABLE execution_view_steps ADD CONSTRAINT uq_execution_view_steps_attempt "
            "UNIQUE NULLS NOT DISTINCT (run_id, step_id, attempt_id)"
        ),
        "DROP INDEX ix_execution_view_steps_parent",
        "ALTER TABLE execution_view_steps DROP CONSTRAINT fk_execution_view_steps_run_scope",
        "ALTER TABLE execution_view_steps ALTER CONSTRAINT fk_execution_view_steps_run_scope DEFERRABLE INITIALLY DEFERRED",
        (
            "CREATE SCHEMA wrong_scope; CREATE TABLE wrong_scope.execution_view_runs "
            "(run_id uuid, scope_key varchar(261), UNIQUE(run_id, scope_key)); "
            "ALTER TABLE execution_view_steps DROP CONSTRAINT fk_execution_view_steps_run_scope; "
            "ALTER TABLE execution_view_steps ADD CONSTRAINT fk_execution_view_steps_run_scope "
            "FOREIGN KEY (run_id, scope_key) REFERENCES wrong_scope.execution_view_runs(run_id,scope_key) ON DELETE RESTRICT"
        ),
    ],
)
def test_existing_wrong_shape_is_rejected(isolated_database, mutation):
    engine, config = isolated_database
    command.upgrade(config, "0002execution_view")
    from app.infrastructure.migrations.execution_view_ddl import upgrade_execution_view

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            _configure_migration(connection)
            connection.execute(sa.text(mutation))
            with pytest.raises(RuntimeError, match=r"schema mismatch.*execution_view_steps"):
                upgrade_execution_view(connection)
        finally:
            transaction.rollback()
