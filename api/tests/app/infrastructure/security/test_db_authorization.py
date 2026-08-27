from pathlib import Path

from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope, Principal
from app.infrastructure.security import db_authorization
from app.infrastructure.security.db_authorization import (
    configure_sync_system_authorization,
)


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def execute(self, statement, parameters):
        self.calls.append((str(statement), parameters))


def test_alembic_authorization_is_transaction_local_and_explicitly_system():
    connection = _Connection()

    configure_sync_system_authorization(
        connection,
        actor="alembic-migration",
        signing_secret="test-database-authorization-signing-secret",
    )

    assert len(connection.calls) == 1
    statement, parameters = connection.calls[0]
    assert "set_config('app.auth_mode', :auth_mode, true)" in statement
    assert "set_config('app.system_actor', :system_actor, true)" in statement
    assert "set_config('app.auth_signature', :auth_signature, true)" in statement
    assert len(parameters["auth_signature"]) == 64
    assert {key: value for key, value in parameters.items() if key != "auth_signature"} == {
        "auth_mode": "system",
        "user_id": "",
        "team_id": "",
        "is_admin": "false",
        "request_id": "",
        "system_actor": "alembic-migration",
    }


def test_alembic_online_runner_binds_system_authorization_before_migrations():
    env_source = (Path(__file__).parents[4] / "alembic" / "env.py").read_text(encoding="utf-8")

    configure_index = env_source.index("configure_sync_system_authorization(")
    migrate_index = env_source.index("context.run_migrations()", configure_index)

    assert configure_index < migrate_index
    assert "app.rls_signing_secret" in env_source


def test_sync_database_clients_bind_signed_user_authorization() -> None:
    connection = _Connection()
    context = AuthorizationContext.for_principal(
        Principal(user_id="user-1"),
        scope=OwnerScope.personal("user-1"),
        request_id="request-1",
    )

    configure = getattr(db_authorization, "configure_sync_authorization", None)
    assert configure is not None, "sync database clients need the signed context API"
    configure(connection, context, signing_secret="test-signing-secret")

    _, parameters = connection.calls[0]
    assert len(parameters["auth_signature"]) == 64
    assert parameters["auth_mode"] == "user"
    assert parameters["user_id"] == "user-1"
    assert parameters["request_id"] == "request-1"
