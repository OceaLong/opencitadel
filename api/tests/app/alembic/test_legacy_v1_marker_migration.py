"""Migration contract for the narrow legacy-v1 compatibility window."""
import importlib.util
from io import StringIO
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory


PATH = Path(__file__).parents[3] / "alembic" / "versions" / "b8d9e0f1a2b3_mark_migrated_legacy_v1_resources.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("legacy_marker", PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _offline_sql(action: str) -> str:
    module = _load_migration()
    output = StringIO()
    context = MigrationContext.configure(url="postgresql://", opts={"as_sql": True, "output_buffer": output})
    previous = module.op
    module.op = Operations(context)
    try:
        getattr(module, action)()
    finally:
        module.op = previous
    return output.getvalue()


def test_b8_remains_in_the_single_linear_chain_after_b7():
    script = ScriptDirectory.from_config(Config("alembic.ini"))

    assert script.get_heads() == ["d8e9f0a1b2c3"]
    assert script.get_revision("d8e9f0a1b2c3").down_revision == "c7d8e9f0a1b2"
    assert script.get_revision("c7d8e9f0a1b2").down_revision == "b8d9e0f1a2b3"
    assert script.get_revision("b8d9e0f1a2b3").down_revision == "b7c8d9e0f1a2"
    assert script.get_revision("b7c8d9e0f1a2").down_revision == "b6c7d8e9f0a1"


def test_marker_offline_upgrade_has_false_default_and_narrow_backfill():
    sql = _offline_sql("upgrade")

    assert "ADD COLUMN legacy_v1_migrated BOOLEAN DEFAULT false NOT NULL" in sql
    assert "UPDATE codebases SET legacy_v1_migrated = true WHERE status = 'ready'" in sql
    assert "UPDATE knowledge_bases SET legacy_v1_migrated = true WHERE status = 'ready'" in sql


def test_marker_offline_downgrade_removes_both_columns_in_reverse_order():
    sql = _offline_sql("downgrade")

    knowledge_drop = "ALTER TABLE knowledge_bases DROP COLUMN legacy_v1_migrated"
    codebase_drop = "ALTER TABLE codebases DROP COLUMN legacy_v1_migrated"
    assert knowledge_drop in sql
    assert codebase_drop in sql
    assert sql.index(knowledge_drop) < sql.index(codebase_drop)


def test_backfill_marks_only_preexisting_ready_rows_and_default_stays_false():
    module = _load_migration()
    engine = sa.create_engine("sqlite://")
    metadata = sa.MetaData()
    for table_name in ("codebases", "knowledge_bases"):
        sa.Table(
            table_name,
            metadata,
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("status", sa.String(), nullable=False),
        )
    metadata.create_all(engine)

    with engine.begin() as connection:
        for table_name in ("codebases", "knowledge_bases"):
            connection.execute(sa.text(
                f"INSERT INTO {table_name} (id, status) "
                "VALUES ('ready-before', 'ready'), ('building-before', 'pending')"
            ))

        context = MigrationContext.configure(connection)
        previous = module.op
        module.op = Operations(context)
        try:
            module.upgrade()
        finally:
            module.op = previous

        for table_name in ("codebases", "knowledge_bases"):
            connection.execute(sa.text(
                f"INSERT INTO {table_name} (id, status) "
                "VALUES ('ready-after', 'ready')"
            ))
            rows = connection.execute(sa.text(
                f"SELECT id, legacy_v1_migrated FROM {table_name} ORDER BY id"
            )).all()
            assert rows == [
                ("building-before", 0),
                ("ready-after", 0),
                ("ready-before", 1),
            ]
