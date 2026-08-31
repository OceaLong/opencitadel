import hashlib
import hmac
from pathlib import Path

from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope, Principal
from app.domain.models.user import GlobalRole
from app.infrastructure.security import db_authorization
from app.infrastructure.security.db_authorization import (
    configure_sync_authorization,
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
        "is_auditor": "false",
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
    assert parameters["is_auditor"] == "false"


def test_auditor_principal_binds_signed_is_auditor_claim() -> None:
    connection = _Connection()
    context = AuthorizationContext.for_principal(
        Principal(user_id="auditor-1", global_role=GlobalRole.AUDITOR),
        scope=OwnerScope.personal("auditor-1"),
    )

    configure_sync_authorization(connection, context, signing_secret="test-signing-secret")

    _, parameters = connection.calls[0]
    assert parameters["is_auditor"] == "true"
    # An auditor is not an admin — the two claims are independent.
    assert parameters["is_admin"] == "false"


def test_signed_signature_matches_sql_function_payload_order() -> None:
    # Make-or-break coupling guard: the Python signer and the RLS validator
    # function opencitadel_authorization_valid() must hash the SAME payload.
    # Here we recompute the signature exactly the way the SQL function concats
    # its claims (chr(31) separator, is_auditor last) and assert equality. If
    # anyone reorders or adds a claim on only one side, this fails.
    secret = "test-signing-secret"
    connection = _Connection()
    context = AuthorizationContext.for_principal(
        Principal(user_id="user-9", global_role=GlobalRole.AUDITOR),
        scope=OwnerScope.personal("user-9"),
        request_id="req-42",
    )
    configure_sync_authorization(connection, context, signing_secret=secret)
    _, parameters = connection.calls[0]

    sql_payload = chr(31).join(
        [
            parameters["auth_mode"],
            parameters["user_id"],
            parameters["team_id"],
            parameters["is_admin"],
            parameters["request_id"],
            parameters["system_actor"],
            parameters["is_auditor"],
        ]
    )
    expected = hmac.new(secret.encode(), sql_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    assert parameters["auth_signature"] == expected


def test_migration_function_hashes_is_auditor_as_final_claim() -> None:
    # Ties the greenfield validator function's SQL claim order to the signer
    # above: is_auditor must be the last field opencitadel_authorization_valid()
    # concats, in the same order the Python signer hashes.
    migration = (
        Path(__file__).parents[4] / "alembic" / "versions" / "0001greenfield_initial.py"
    ).read_text(encoding="utf-8")

    start = migration.index("claims := concat(")
    block = migration[start : migration.index(");", start)]
    order = [
        field
        for field in (
            "app.auth_mode",
            "app.user_id",
            "app.team_id",
            "app.is_admin",
            "app.request_id",
            "app.system_actor",
            "app.is_auditor",
        )
        if field in block
    ]
    assert order == [
        "app.auth_mode",
        "app.user_id",
        "app.team_id",
        "app.is_admin",
        "app.request_id",
        "app.system_actor",
        "app.is_auditor",
    ]
    assert block.rindex("app.is_auditor") > block.rindex("app.system_actor")
