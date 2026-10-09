import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

from alembic import command
from tests.app.alembic.test_execution_view_migration import (  # noqa: F401
    _configure_migration,
    isolated_database,
)


def migration():
    spec = importlib.util.spec_from_file_location(
        "view_queries_migration", Path("alembic/versions/0002view_queries.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_forward_upgrade_and_precreated_shape_validation(isolated_database):  # noqa: F811
    engine, config = isolated_database
    command.upgrade(config, "0002execution_view")
    with engine.begin() as connection:
        _configure_migration(connection)
        count = connection.scalar(sa.text("SELECT count(*) FROM execution_view_observations"))
    command.upgrade(config, "head")
    with engine.begin() as connection:
        _configure_migration(connection)
        assert (
            connection.scalar(sa.text("SELECT count(*) FROM execution_view_observations")) == count
        )
        # A precreated exact schema must validate without destructive recreation.
        migration().upgrade_connection(connection)
    with engine.begin() as connection:
        _configure_migration(connection)
        connection.execute(
            sa.text(
                "ALTER TABLE execution_view_cohort_runs DROP CONSTRAINT execution_view_cohort_runs_pkey"
            )
        )
        with pytest.raises(RuntimeError, match="schema mismatch"):
            migration().upgrade_connection(connection)


def test_previous_f04_shape_additive_upgrade_preserves_journal(isolated_database):  # noqa: F811
    from datetime import UTC, datetime
    from uuid import uuid4

    from app.infrastructure.models.execution_view import (
        ExecutionRunViewORM,
        ExecutionViewObservationORM,
    )

    engine, config = isolated_database
    command.upgrade(config, "head")
    with engine.begin() as connection:
        _configure_migration(connection)
        # Only empty disposable fix tables are removed to simulate the prior F04 schema.
        connection.execute(sa.text("DROP TABLE execution_view_read_steps"))
        connection.execute(sa.text("DROP TABLE execution_view_read_cuts"))
        connection.execute(
            sa.text("ALTER TABLE execution_view_shadow_runs DROP COLUMN coverage_token")
        )
        run = uuid4()
        now = datetime.now(UTC)
        common = {"owner_user_id": "retained-f04", "team_id": None, "created_by": "test"}
        connection.execute(
            ExecutionRunViewORM.__table__.insert(),
            {
                **common,
                "run_id": run,
                "family": "agent",
                "status": "running",
                "purpose": "unknown",
                "completeness": {},
                "capabilities": [],
                "formal_position": 1,
                "progress_position": 0,
                "observed_order": 1,
                "projection_revision": 1,
                "projector_version": 1,
                "as_of": now,
                "latest_available": now,
            },
        )
        connection.execute(
            ExecutionViewObservationORM.__table__.insert(),
            {
                **common,
                "run_id": run,
                "source_kind": "formal",
                "source_identity": "retained",
                "formal_position": 1,
                "progress_position": 0,
                "observed_order": 1,
                "projection_revision": 1,
                "projector_version": 1,
                "observed_at": now,
                "public_payload": {"facts": []},
            },
        )
        before = connection.scalar(
            sa.text("SELECT row_to_json(o)::text FROM execution_view_observations o")
        )
        migration().upgrade_connection(connection)
        after = connection.scalar(
            sa.text("SELECT row_to_json(o)::text FROM execution_view_observations o")
        )
        assert after == before
        assert sa.inspect(connection).has_table("execution_view_read_steps")
        assert "coverage_token" in {
            c["name"] for c in sa.inspect(connection).get_columns("execution_view_shadow_runs")
        }
