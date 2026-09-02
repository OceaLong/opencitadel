"""Standalone database migration entrypoint (run once per deploy)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from app.infrastructure.logging import setup_logging
from core.config import (
    DeploymentSettings,
    load_deployment_settings,
    sqlalchemy_sync_migration_database_uri,
)

logger = logging.getLogger(__name__)
_MIGRATION_LOCK_ID = 0x4F50454E43495441


@contextmanager
def migration_lock(settings: DeploymentSettings) -> Iterator[None]:
    """Serialize the complete schema-and-seed operation across deployers."""
    engine = create_engine(
        sqlalchemy_sync_migration_database_uri(settings),
        pool_pre_ping=True,
    )
    try:
        with engine.connect() as connection:
            connection.execute(
                text("SELECT pg_advisory_lock(:lock_id)"),
                {"lock_id": _MIGRATION_LOCK_ID},
            )
            try:
                yield
            finally:
                try:
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_id)"),
                        {"lock_id": _MIGRATION_LOCK_ID},
                    )
                except (OSError, RuntimeError, ValueError):
                    logger.exception("Failed to release the database migration lock")
    finally:
        engine.dispose()


def main() -> None:
    settings = load_deployment_settings()
    setup_logging(settings)
    with migration_lock(settings):
        alembic_cfg = Config("alembic.ini")
        alembic_cfg.attributes["deployment_settings"] = settings
        command.upgrade(alembic_cfg, "head")
        print("Database schema migrations applied successfully.")


if __name__ == "__main__":
    main()
