"""Rotate the database-side RLS authorization signing secret (run per rotation).

The single-row ``execution_authorization_secrets`` table is stamped once by the
initial migration and has no other update path. If
``DATABASE_AUTHORIZATION_SIGNING_SECRET`` changes after that stamp, every
RLS-protected statement fails signature validation. This entrypoint updates the
database row to match the currently configured secret and verifies a signed
probe inside the same transaction, rolling back on any failure. Re-running with
a matching secret is a no-op, so the command is safe to invoke repeatedly.
"""

from __future__ import annotations

from sqlalchemy import create_engine, text

from app.infrastructure.security.db_authorization import (
    configure_sync_system_authorization,
)
from core.config import load_deployment_settings, sqlalchemy_sync_migration_database_uri

_ACTOR = "rotate:db-signing-secret"


def main() -> None:
    settings = load_deployment_settings()
    secret = settings.database_authorization_signing_secret
    engine = create_engine(
        sqlalchemy_sync_migration_database_uri(settings),
        pool_pre_ping=True,
    )
    try:
        with engine.begin() as connection:
            current = connection.execute(
                text(
                    "SELECT signing_secret FROM execution_authorization_secrets "
                    "WHERE singleton = true FOR UPDATE"
                )
            ).scalar_one()
            if current == secret:
                print("Database signing secret already matches the configured value.")
            else:
                connection.execute(
                    text(
                        "UPDATE execution_authorization_secrets "
                        "SET signing_secret = :secret WHERE singleton = true"
                    ),
                    {"secret": secret},
                )
                print("Database signing secret updated to the configured value.")
            configure_sync_system_authorization(
                connection,
                actor=_ACTOR,
                signing_secret=secret,
            )
            valid = connection.execute(
                text("SELECT opencitadel_authorization_valid()")
            ).scalar_one()
            if not valid:
                raise RuntimeError(
                    "signed authorization probe failed after rotation; transaction rolled back"
                )
        print(
            "Signed authorization probe passed; rotation committed. "
            "Restart api and execution-kernel replicas so every process signs "
            "with the new secret."
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
