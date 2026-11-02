"""Embedding-space shape is valid for both live-metadata and forward installs."""

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect


def test_embedding_space_revision_follows_existing_head():
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_revision(
        "0031phase1_embedding_space"
    )
    assert revision is not None
    assert revision.down_revision == "0030evaluation_judge_history"


@pytest.mark.usefixtures("postgres_integration")
def test_embedding_space_schema_preserves_unidentified_legacy_vectors():
    from core.config import load_deployment_settings, sqlalchemy_sync_database_uri

    engine = create_engine(sqlalchemy_sync_database_uri(load_deployment_settings()))
    try:
        inspector = inspect(engine)
        versions = {
            column["name"]: column for column in inspector.get_columns("knowledge_base_versions")
        }
        memories = {column["name"]: column for column in inspector.get_columns("memory_entries")}
        assert versions["embedding_space"]["nullable"]
        assert versions["embedding_space_digest"]["nullable"]
        assert versions["embedding_space_digest"]["type"].length == 64
        assert memories["embedding_space_digest"]["nullable"]
        assert memories["embedding_space_digest"]["default"] is None
        assert any(
            index["name"] == "ix_memory_entries_embedding_space"
            and index["column_names"] == ["embedding_space_digest"]
            for index in inspector.get_indexes("memory_entries")
        )
    finally:
        engine.dispose()
