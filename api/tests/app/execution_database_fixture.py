"""Fresh database fixture for tests of global execution queue discovery."""

import pytest
from sqlalchemy.engine import make_url

from alembic import command
from core.config import load_deployment_settings


@pytest.fixture
def isolated_execution_database(isolated_database, monkeypatch):
    """Keep global scans independent without changing runtime login authority."""
    engine, config = isolated_database
    command.upgrade(config, "head")
    settings = load_deployment_settings()
    runtime_url = make_url(settings.sqlalchemy_database_uri).set(database=engine.url.database)
    monkeypatch.setenv("SQLALCHEMY_DATABASE_URI", runtime_url.render_as_string(hide_password=False))
    monkeypatch.setenv(
        "SQLALCHEMY_MIGRATION_DATABASE_URI", engine.url.render_as_string(hide_password=False)
    )
