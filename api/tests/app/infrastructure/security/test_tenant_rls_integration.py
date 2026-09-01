"""Database-backed Runtime Policy RLS and privilege proofs."""

import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope, Principal
from app.domain.models.user import GlobalRole
from app.domain.runtime_policy import ExecutionPolicy, OperationsPolicy, policy_digest
from app.infrastructure.security.db_authorization import (
    configure_sync_authorization,
    configure_sync_system_authorization,
)
from core.config import (
    load_deployment_settings,
    sqlalchemy_sync_database_uri,
    sqlalchemy_sync_migration_database_uri,
)

pytestmark = pytest.mark.usefixtures("postgres_integration")

_POLICY_TABLES = (
    "execution_policy_revisions",
    "operations_policy_revisions",
    "runtime_policy_heads",
)


def _seed_runtime_policy_head() -> tuple[UUID, UUID]:
    execution_id = uuid4()
    operations_id = uuid4()
    execution = ExecutionPolicy()
    operations = OperationsPolicy()
    settings = load_deployment_settings()
    engine = create_engine(sqlalchemy_sync_migration_database_uri(settings))
    try:
        with engine.begin() as connection:
            configure_sync_system_authorization(
                connection,
                actor="runtime-policy-rls-test-seed",
                signing_secret=settings.session_secret,
            )
            connection.execute(
                text(
                    """
                    INSERT INTO execution_policy_revisions
                        (id, schema_version, payload, digest, created_by, note)
                    VALUES
                        (:id, 1, CAST(:payload AS jsonb), :digest, :actor, :note)
                    """
                ),
                {
                    "id": execution_id,
                    "payload": json.dumps(execution.model_dump(mode="json")),
                    "digest": policy_digest(1, execution),
                    "actor": "admin-rls-test",
                    "note": "RLS execution seed",
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO operations_policy_revisions
                        (id, schema_version, payload, digest, created_by, note)
                    VALUES
                        (:id, 1, CAST(:payload AS jsonb), :digest, :actor, :note)
                    """
                ),
                {
                    "id": operations_id,
                    "payload": json.dumps(operations.model_dump(mode="json")),
                    "digest": policy_digest(1, operations),
                    "actor": "admin-rls-test",
                    "note": "RLS operations seed",
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO runtime_policy_heads
                        (id, version, execution_revision_id,
                         operations_revision_id, updated_by)
                    VALUES
                        ('global', 1, :execution_id, :operations_id, :actor)
                    ON CONFLICT (id) DO UPDATE SET
                        version = runtime_policy_heads.version + 1,
                        execution_revision_id = EXCLUDED.execution_revision_id,
                        operations_revision_id = EXCLUDED.operations_revision_id,
                        updated_by = EXCLUDED.updated_by,
                        updated_at = CURRENT_TIMESTAMP(0)
                    """
                ),
                {
                    "execution_id": execution_id,
                    "operations_id": operations_id,
                    "actor": "admin-rls-test",
                },
            )
    finally:
        engine.dispose()
    return execution_id, operations_id


@pytest.fixture
def seeded_runtime_policy(postgres_integration) -> tuple[UUID, UUID]:
    del postgres_integration
    return _seed_runtime_policy_head()


def _visible_policy_counts(connection) -> tuple[int, ...]:
    return tuple(
        connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
        for table in _POLICY_TABLES
    )


def test_runtime_policy_is_visible_only_to_system_or_admin(
    seeded_runtime_policy,
) -> None:
    del seeded_runtime_policy
    settings = load_deployment_settings()
    engine = create_engine(sqlalchemy_sync_database_uri(settings))
    try:
        with engine.connect() as connection:
            role = connection.execute(
                text(
                    """
                    SELECT current_user, rolsuper, rolbypassrls
                    FROM pg_roles
                    WHERE rolname = current_user
                    """
                )
            ).one()
            assert role == (
                load_deployment_settings().postgres_user,
                False,
                False,
            )

            for table in _POLICY_TABLES:
                rls = connection.execute(
                    text(
                        """
                        SELECT relrowsecurity, relforcerowsecurity
                        FROM pg_class
                        WHERE oid = CAST(:table AS regclass)
                        """
                    ),
                    {"table": table},
                ).one()
                assert rls == (True, True)

            assert _visible_policy_counts(connection) == (0, 0, 0)
            connection.rollback()

            with connection.begin():
                configure_sync_authorization(
                    connection,
                    AuthorizationContext.for_principal(
                        Principal(user_id="runtime-policy-user"),
                        scope=OwnerScope.personal("runtime-policy-user"),
                    ),
                    signing_secret=settings.session_secret,
                )
                assert _visible_policy_counts(connection) == (0, 0, 0)

            with connection.begin():
                configure_sync_authorization(
                    connection,
                    AuthorizationContext.for_principal(
                        Principal(
                            user_id="runtime-policy-admin",
                            global_role=GlobalRole.ADMIN,
                        ),
                        scope=OwnerScope.personal("runtime-policy-admin"),
                    ),
                    signing_secret=settings.session_secret,
                )
                assert all(count >= 1 for count in _visible_policy_counts(connection))

            with connection.begin():
                configure_sync_authorization(
                    connection,
                    AuthorizationContext.system("runtime-policy-reader"),
                    signing_secret=settings.session_secret,
                )
                assert all(count >= 1 for count in _visible_policy_counts(connection))
    finally:
        engine.dispose()


def test_auditor_reads_across_owners_but_cannot_write(
    seeded_runtime_policy,
) -> None:
    """The greenfield RLS baseline gives the auditor role read-all / no-write.

    A regular user sees no control-plane policy (proved above); an auditor must
    now SELECT it (compliance read) while every write predicate still rejects
    them. This exercises the signed is_auditor claim end-to-end against the
    migrated opencitadel_authorization_valid() function.
    """
    del seeded_runtime_policy
    settings = load_deployment_settings()
    engine = create_engine(sqlalchemy_sync_database_uri(settings))
    auditor = AuthorizationContext.for_principal(
        Principal(user_id="policy-auditor", global_role=GlobalRole.AUDITOR),
        scope=OwnerScope.personal("policy-auditor"),
    )
    try:
        with engine.connect() as connection:
            # Auditor may read the seeded control-plane rows across owners.
            with connection.begin():
                configure_sync_authorization(
                    connection, auditor, signing_secret=settings.session_secret
                )
                assert all(count >= 1 for count in _visible_policy_counts(connection))

            # ...but the write predicate rejects the auditor: an UPDATE that
            # would match without RLS touches zero rows under RLS.
            with connection.begin():
                configure_sync_authorization(
                    connection, auditor, signing_secret=settings.session_secret
                )
                result = connection.execute(
                    text(
                        "UPDATE runtime_policy_heads "
                        "SET updated_by = 'auditor-should-not-write' "
                        "WHERE id = 'global'"
                    )
                )
                assert result.rowcount == 0
    finally:
        engine.dispose()


def test_auditor_reads_private_tenant_rows_across_owners(
    postgres_integration,
) -> None:
    """The read-all wrap also covers private root tables, not just policy tables.

    A row owned by owner-a is invisible to an unrelated regular user but visible
    to an auditor, and the auditor still cannot mutate it.
    """
    del postgres_integration
    settings = load_deployment_settings()
    admin_engine = create_engine(sqlalchemy_sync_migration_database_uri(settings))
    session_id = str(uuid4())
    try:
        with admin_engine.begin() as connection:
            configure_sync_system_authorization(
                connection,
                actor="auditor-rls-private-seed",
                signing_secret=settings.session_secret,
            )
            # sessions.owner_user_id is a real foreign key into users, so the
            # owning account must exist before the private row can be seeded.
            connection.execute(
                text(
                    """
                    INSERT INTO users (id, email, username)
                    VALUES ('owner-a', 'owner-a@example.com', 'owner-a')
                    ON CONFLICT (id) DO NOTHING
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO sessions (id, owner_user_id, title, status)
                    VALUES (:id, 'owner-a', 'owned by a', 'active')
                    """
                ),
                {"id": session_id},
            )
    finally:
        admin_engine.dispose()

    engine = create_engine(sqlalchemy_sync_database_uri(settings))

    def _count_visible(connection) -> int:
        return connection.execute(
            text("SELECT count(*) FROM sessions WHERE id = :id"),
            {"id": session_id},
        ).scalar_one()

    try:
        with engine.connect() as connection:
            # A regular, non-owner user must not see owner-a's session.
            with connection.begin():
                configure_sync_authorization(
                    connection,
                    AuthorizationContext.for_principal(
                        Principal(user_id="stranger"),
                        scope=OwnerScope.personal("stranger"),
                    ),
                    signing_secret=settings.session_secret,
                )
                assert _count_visible(connection) == 0

            # An auditor sees it (read-all) but cannot update it (no-write).
            auditor = AuthorizationContext.for_principal(
                Principal(user_id="private-auditor", global_role=GlobalRole.AUDITOR),
                scope=OwnerScope.personal("private-auditor"),
            )
            with connection.begin():
                configure_sync_authorization(
                    connection, auditor, signing_secret=settings.session_secret
                )
                assert _count_visible(connection) == 1
                result = connection.execute(
                    text("UPDATE sessions SET title = 'tampered' WHERE id = :id"),
                    {"id": session_id},
                )
                assert result.rowcount == 0
    finally:
        with engine.begin() as connection:
            configure_sync_system_authorization(
                connection,
                actor="auditor-rls-private-cleanup",
                signing_secret=settings.session_secret,
            )
            connection.execute(text("DELETE FROM sessions WHERE id = :id"), {"id": session_id})
            connection.execute(text("DELETE FROM users WHERE id = 'owner-a'"))
        engine.dispose()


def test_runtime_policy_database_privileges_are_least_authority(
    seeded_runtime_policy,
) -> None:
    del seeded_runtime_policy
    engine = create_engine(sqlalchemy_sync_database_uri(load_deployment_settings()))
    try:
        with engine.connect() as connection:
            for table in _POLICY_TABLES[:2]:
                assert connection.execute(
                    text("SELECT has_table_privilege(current_user, :table, 'SELECT')"),
                    {"table": table},
                ).scalar_one()
                assert connection.execute(
                    text("SELECT has_table_privilege(current_user, :table, 'INSERT')"),
                    {"table": table},
                ).scalar_one()
                assert not connection.execute(
                    text("SELECT has_table_privilege(current_user, :table, 'UPDATE')"),
                    {"table": table},
                ).scalar_one()
                assert not connection.execute(
                    text("SELECT has_table_privilege(current_user, :table, 'DELETE')"),
                    {"table": table},
                ).scalar_one()

            assert connection.execute(
                text(
                    "SELECT has_table_privilege(current_user, "
                    "'runtime_policy_heads', 'SELECT, UPDATE')"
                )
            ).scalar_one()
            assert not connection.execute(
                text(
                    "SELECT has_table_privilege(current_user, "
                    "'runtime_policy_heads', 'INSERT, DELETE')"
                )
            ).scalar_one()

            for table in _POLICY_TABLES:
                assert connection.execute(
                    text(
                        "SELECT has_table_privilege("
                        "'opencitadel_execution_kernel', :table, 'SELECT')"
                    ),
                    {"table": table},
                ).scalar_one()
                assert not connection.execute(
                    text(
                        "SELECT has_table_privilege("
                        "'opencitadel_execution_kernel', :table, "
                        "'INSERT, UPDATE, DELETE')"
                    ),
                    {"table": table},
                ).scalar_one()
    finally:
        engine.dispose()


@pytest.mark.parametrize("statement", ["UPDATE", "DELETE"])
def test_runtime_policy_revisions_are_database_immutable(
    seeded_runtime_policy,
    statement: str,
) -> None:
    execution_id, _ = seeded_runtime_policy
    engine = create_engine(sqlalchemy_sync_migration_database_uri(load_deployment_settings()))
    try:
        with pytest.raises(DBAPIError, match="runtime policy revisions are immutable"):
            _mutate_execution_revision(engine, statement, execution_id)
    finally:
        engine.dispose()


def _mutate_execution_revision(engine, statement: str, execution_id: UUID) -> None:
    with engine.begin() as connection:
        configure_sync_system_authorization(
            connection,
            actor="runtime-policy-immutability-test",
            signing_secret=load_deployment_settings().session_secret,
        )
        if statement == "UPDATE":
            connection.execute(
                text("UPDATE execution_policy_revisions SET note = note WHERE id = :id"),
                {"id": execution_id},
            )
        else:
            connection.execute(
                text("DELETE FROM execution_policy_revisions WHERE id = :id"),
                {"id": execution_id},
            )
