"""Opt-in, small real-PostgreSQL semantics check for candidate OWNERS pages.

This does not dispatch pagination in the production inventory reader or qualify
the 100,000-row capacity workload. The fixture schema is unique and disposable.
"""

import os
from uuid import UUID, uuid4

import pytest
from scripts.acceptance.capacity_io import canonical_digest
from scripts.execution_capacity.inventory_sql import (
    OWNERS,
    OWNERS_PAGE_AFTER,
    OWNERS_PAGE_FIRST,
    plain,
)
from sqlalchemy import create_engine, text

from core.config import load_deployment_settings, sqlalchemy_sync_migration_database_uri


def _rows(connection, statement, parameters=None):
    return [dict(row) for row in connection.execute(text(statement), parameters or {}).mappings()]


def test_fixed_owners_pages_equal_original_with_one_pg_snapshot():
    if os.getenv("OPENCITADEL_REQUIRE_POSTGRES_TESTS") != "1":
        pytest.skip("explicit isolated PostgreSQL test opt-in required")
    settings = load_deployment_settings()
    if settings.env.lower() != "test":
        pytest.fail("OWNERS page semantics test requires a test deployment")

    engine = create_engine(
        sqlalchemy_sync_migration_database_uri(settings),
        connect_args={"connect_timeout": 3},
        pool_pre_ping=True,
    )
    schema = "a06_owners_" + uuid4().hex
    projected = UUID("00000000-0000-0000-0000-000000000001")
    projected_other = UUID("00000000-0000-0000-0000-000000000002")
    missing = UUID("00000000-0000-0000-0000-000000000003")
    inserted = UUID("00000000-0000-0000-0000-000000000004")
    created = False
    try:
        with engine.begin() as setup:
            setup.execute(text(f"CREATE SCHEMA {schema}"))
            created = True
            setup.execute(
                text(
                    f"CREATE TABLE {schema}.execution_stream_owners ("
                    "stream_type varchar(64) NOT NULL, stream_id varchar(255) NOT NULL, "
                    "owner_scope_key varchar(261) NOT NULL, "
                    "PRIMARY KEY (stream_type, stream_id))"
                )
            )
            setup.execute(
                text(
                    f"CREATE TABLE {schema}.execution_run_projection ("
                    "run_id uuid PRIMARY KEY, source_entity_type varchar(64) NOT NULL, "
                    "source_entity_id varchar(255) NOT NULL, correlation_id uuid NOT NULL, "
                    "stream_version integer NOT NULL, terminal boolean NOT NULL)"
                )
            )
            owners = [
                ("activity", "same"),
                ("run", str(projected)),
                ("run", str(projected_other)),
                ("run", str(missing)),
                ("run", "A"),
                ("run", "a"),
                ("run", "same"),
                ("run", "ä"),
                ("workflow", "same"),
            ]
            setup.execute(
                text(
                    f"INSERT INTO {schema}.execution_stream_owners "
                    "(stream_type,stream_id,owner_scope_key) "
                    "VALUES (:stream_type,:stream_id,:scope)"
                ),
                [
                    {"stream_type": kind, "stream_id": key, "scope": "user:fixture"}
                    for kind, key in owners
                ],
            )
            setup.execute(
                text(
                    f"INSERT INTO {schema}.execution_run_projection "
                    "(run_id,source_entity_type,source_entity_id,correlation_id,stream_version,terminal) "
                    "VALUES (:run_id,:source_type,:source_id,:correlation,:version,:terminal)"
                ),
                [
                    {
                        "run_id": run_id,
                        "source_type": "capacity_fixture",
                        "source_id": str(run_id),
                        "correlation": run_id,
                        "version": ordinal,
                        "terminal": ordinal == 2,
                    }
                    for ordinal, run_id in enumerate((projected, projected_other), start=1)
                ],
            )

        with engine.connect() as reader:
            reader.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            reader.execute(text(f"SET LOCAL search_path TO {schema}, pg_catalog"))
            assert (
                reader.execute(text("SHOW transaction_isolation")).scalar_one() == "repeatable read"
            )
            assert reader.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
            collation = reader.execute(
                text(
                    "SELECT datcollate FROM pg_catalog.pg_database WHERE datname=current_database()"
                )
            ).scalar_one()
            assert collation

            # Match the installed source key columns' effective PostgreSQL
            # collation; Python's tuple order is not a database oracle.
            def key_collations(table):
                return dict(
                    reader.execute(
                        text(
                            "SELECT attname,attcollation FROM pg_catalog.pg_attribute "
                            "WHERE attrelid=to_regclass(:table_name) "
                            "AND attname IN ('stream_type','stream_id')"
                        ),
                        {"table_name": table},
                    ).all()
                )

            actual_collations = key_collations("public.execution_stream_owners")
            assert set(actual_collations) == {"stream_type", "stream_id"}
            assert key_collations(f"{schema}.execution_stream_owners") == actual_collations

            # The original unpaged SQL is a separate same-snapshot reference.
            original = _rows(reader, OWNERS)
            original_count = reader.execute(
                text(
                    "SELECT count(*) FROM execution_stream_owners s "
                    "LEFT JOIN execution_run_projection p "
                    "ON s.stream_type='run' AND p.run_id::text=s.stream_id"
                )
            ).scalar_one()
            assert original_count == len(owners) == len(original)
            assert len({(r["stream_type"], r["stream_id"]) for r in original}) == len(original)
            assert sum(r["source_entity_type"] is not None for r in original) == 2
            assert all(
                r["source_entity_type"] is None
                for r in original
                if r["stream_type"] != "run" or r["stream_id"] == str(missing)
            )
            reference_digest = canonical_digest(plain(original))

            # A committed row after the reference query must stay outside this
            # REPEATABLE READ snapshot even though pagination dispatches later.
            with engine.begin() as writer:
                writer.execute(
                    text(
                        f"INSERT INTO {schema}.execution_stream_owners "
                        "(stream_type,stream_id,owner_scope_key) "
                        "VALUES ('run',:run_id,'user:fixture')"
                    ),
                    {"run_id": str(inserted)},
                )

            pages = []
            cursor = None
            for _ in range(16):
                parameters = {"page_size": 2}
                statement = OWNERS_PAGE_FIRST
                if cursor is not None:
                    parameters.update(after_type=cursor[0], after_id=cursor[1])
                    statement = OWNERS_PAGE_AFTER
                page = _rows(reader, statement, parameters)
                assert len(page) <= 2
                if not page:
                    break
                pages.extend(page)
                cursor = (page[-1]["stream_type"], page[-1]["stream_id"])
            else:
                pytest.fail("bounded OWNERS pages never reached explicit EOF")

            assert len(pages) == original_count
            assert canonical_digest(plain(pages)) == reference_digest
            assert pages == original
            reader.rollback()

        with engine.connect() as later:
            later.execute(text(f"SET LOCAL search_path TO {schema}, pg_catalog"))
            assert (
                later.execute(text("SELECT count(*) FROM execution_stream_owners")).scalar_one()
                == len(owners) + 1
            )
    finally:
        try:
            if created:
                with engine.begin() as cleanup:
                    cleanup.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        finally:
            engine.dispose()
