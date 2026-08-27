"""PostgreSQL proof for execution-kernel tenant isolation and immutability."""

from collections.abc import Callable
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.domain.models.authorization import AuthorizationContext
from app.domain.models.scope import OwnerScope, Principal
from app.domain.models.team import TeamRole
from app.domain.models.user import GlobalRole
from app.infrastructure.security.db_authorization import (
    configure_sync_authorization,
)
from core.config import load_deployment_settings, sqlalchemy_sync_migration_database_uri
from tests.app.execution_test_support import execution_kernel_database_uri

APPEND_ONLY_EXECUTION_TABLES = (
    "execution_stream_owners",
    "execution_events",
)
MUTABLE_EXECUTION_TABLES = (
    "execution_command_inbox",
    "execution_outbox",
    "execution_scheduled_commands",
    "execution_activity_tasks",
    "execution_snapshots",
    "execution_projector_checkpoints",
)
EXECUTION_TABLES = APPEND_ONLY_EXECUTION_TABLES + MUTABLE_EXECUTION_TABLES


def _cleanup_scopes(
    *,
    owner_user_ids: tuple[str, ...] = (),
    team_ids: tuple[str, ...] = (),
) -> None:
    engine = create_engine(sqlalchemy_sync_migration_database_uri(load_deployment_settings()))
    try:
        with engine.begin() as connection:
            _set_context(connection, mode="system")
            parameters = {
                "owner_user_ids": list(owner_user_ids),
                "team_ids": list(team_ids),
            }
            for table in reversed(MUTABLE_EXECUTION_TABLES):
                connection.execute(
                    text(
                        f"DELETE FROM {table} "
                        "WHERE owner_user_id = ANY(:owner_user_ids) "
                        "OR team_id = ANY(:team_ids)"
                    ),
                    parameters,
                )
            connection.execute(
                text("ALTER TABLE execution_events DISABLE TRIGGER execution_events_immutable")
            )
            connection.execute(
                text(
                    "DELETE FROM execution_events "
                    "WHERE owner_user_id = ANY(:owner_user_ids) "
                    "OR team_id = ANY(:team_ids)"
                ),
                parameters,
            )
            connection.execute(
                text(
                    "ALTER TABLE execution_stream_owners "
                    "DISABLE TRIGGER execution_stream_owners_immutable"
                )
            )
            connection.execute(
                text(
                    "DELETE FROM execution_stream_owners "
                    "WHERE owner_user_id = ANY(:owner_user_ids) "
                    "OR team_id = ANY(:team_ids)"
                ),
                parameters,
            )
            connection.execute(
                text(
                    "ALTER TABLE execution_stream_owners "
                    "ENABLE TRIGGER execution_stream_owners_immutable"
                )
            )
            connection.execute(
                text("ALTER TABLE execution_events ENABLE TRIGGER execution_events_immutable")
            )
    finally:
        engine.dispose()


def _set_context(
    connection,
    *,
    mode: str,
    user_id: str = "",
    team_id: str = "",
    is_admin: bool = False,
):
    if mode == "system":
        context = AuthorizationContext.system("execution-rls-test")
    else:
        principal = Principal(
            user_id=user_id,
            global_role=GlobalRole.ADMIN if is_admin else GlobalRole.USER,
            team_roles={team_id: TeamRole.MEMBER} if team_id else {},
        )
        scope = OwnerScope.team(user_id, team_id) if team_id else OwnerScope.personal(user_id)
        context = AuthorizationContext.for_principal(principal, scope=scope)
    configure_sync_authorization(
        connection,
        context,
        signing_secret=load_deployment_settings().session_secret,
    )


def _execute_as_kernel(
    connection,
    *,
    mode: str,
    operation: Callable[[], object],
    user_id: str = "",
) -> None:
    with connection.begin():
        connection.execute(text("SET LOCAL ROLE opencitadel_execution_kernel"))
        _set_context(connection, mode=mode, user_id=user_id)
        operation()


def _insert_event(
    connection,
    *,
    event_id,
    stream_id: str,
    owner_user_id: str | None,
    team_id: str | None,
) -> int:
    connection.execute(
        text(
            """
            INSERT INTO execution_stream_owners (
                stream_type, stream_id, owner_user_id, team_id
            ) VALUES (
                'synthetic_run', :stream_id, :owner_user_id, :team_id
            )
            """
        ),
        {
            "stream_id": stream_id,
            "owner_user_id": owner_user_id,
            "team_id": team_id,
        },
    )
    return connection.execute(
        text(
            """
            INSERT INTO execution_events (
                event_id, stream_type, stream_id, stream_version,
                event_type, event_schema_version, public_payload,
                internal_payload, owner_user_id, team_id, correlation_id,
                occurred_at, prev_hash, event_hash
            ) VALUES (
                :event_id, 'synthetic_run', :stream_id, 1,
                'SyntheticRunRequested', 1, '{}'::jsonb,
                '{}'::jsonb, :owner_user_id, :team_id, :correlation_id,
                CURRENT_TIMESTAMP, :prev_hash, :event_hash
            )
            RETURNING position
            """
        ),
        {
            "event_id": event_id,
            "stream_id": stream_id,
            "owner_user_id": owner_user_id,
            "team_id": team_id,
            "correlation_id": uuid4(),
            "prev_hash": "0" * 64,
            "event_hash": "1" * 64,
        },
    ).scalar_one()


def _insert_mutable_execution_rows(
    connection,
    *,
    marker: str,
    personal_position: int,
    team_position: int,
    personal_user_id: str,
    team_id: str,
) -> None:
    for scope, owner_user_id, row_team_id, event_position in (
        ("personal", personal_user_id, None, personal_position),
        ("team", None, team_id, team_position),
    ):
        values = {
            "marker": marker,
            "scope": scope,
            "owner_user_id": owner_user_id,
            "team_id": row_team_id,
            "event_position": event_position,
            "command_id": uuid4(),
            "outbox_id": uuid4(),
            "timer_id": uuid4(),
            "activity_id": uuid4(),
            "correlation_id": uuid4(),
        }
        connection.execute(
            text(
                """
                INSERT INTO execution_command_inbox (
                    command_id, command_type, command_schema_version,
                    stream_type, stream_id, owner_user_id, team_id,
                    correlation_id, issued_at, payload
                ) VALUES (
                    :command_id, 'RequestSyntheticRun', 1,
                    'synthetic_run', :marker || '-' || :scope,
                    :owner_user_id, :team_id, :correlation_id,
                    CURRENT_TIMESTAMP, '{}'::jsonb
                );
                INSERT INTO execution_outbox (
                    outbox_id, event_position, destination, dedupe_key,
                    owner_user_id, team_id
                ) VALUES (
                    :outbox_id, :event_position, 'execution.events',
                    :marker || '-outbox-' || :scope,
                    :owner_user_id, :team_id
                );
                INSERT INTO execution_scheduled_commands (
                    timer_id, due_at, command_envelope,
                    cancellation_event_types, owner_user_id, team_id
                ) VALUES (
                    :timer_id, CURRENT_TIMESTAMP, '{}'::jsonb, '[]'::jsonb,
                    :owner_user_id, :team_id
                );
                INSERT INTO execution_activity_tasks (
                    activity_id, aggregate_type, aggregate_id, activity_type,
                    request_event_position, owner_user_id, team_id,
                    timeout_at, request_digest
                ) VALUES (
                    :activity_id, 'synthetic_run', :marker || '-' || :scope,
                    'SyntheticActivity', :event_position,
                    :owner_user_id, :team_id,
                    CURRENT_TIMESTAMP + INTERVAL '1 minute', 'sha256:test'
                );
                INSERT INTO execution_snapshots (
                    stream_type, stream_id, stream_version,
                    owner_user_id, team_id, state, state_hash,
                    last_event_hash, serializer_version
                ) VALUES (
                    'synthetic_run', :marker || '-' || :scope, 1,
                    :owner_user_id, :team_id, '{}'::jsonb,
                    repeat('1', 64), repeat('2', 64), 1
                );
                INSERT INTO execution_projector_checkpoints (
                    projector_name, owner_scope_key, owner_user_id, team_id,
                    last_position, state, state_hash
                ) VALUES (
                    :marker, :marker || '-' || :scope,
                    :owner_user_id, :team_id, :event_position,
                    '{}'::jsonb, repeat('3', 64)
                )
                """
            ),
            values,
        )


@pytest.mark.usefixtures("_db_schema")
def test_execution_tables_force_rls_and_isolate_personal_and_team_rows() -> None:
    engine = create_engine(execution_kernel_database_uri(async_driver=False))
    try:
        with engine.connect() as connection:
            rls_rows = connection.execute(
                text(
                    """
                    SELECT relname, relrowsecurity, relforcerowsecurity
                    FROM pg_class
                    WHERE relname = ANY(:tables)
                    ORDER BY relname
                    """
                ),
                {"tables": list(EXECUTION_TABLES)},
            ).all()
            assert len(rls_rows) == len(EXECUTION_TABLES)
            assert all(enabled and forced for _, enabled, forced in rls_rows)
            connection.rollback()

            personal_event_id = uuid4()
            team_event_id = uuid4()
            with connection.begin():
                _set_context(connection, mode="system")
                _insert_event(
                    connection,
                    event_id=personal_event_id,
                    stream_id=f"personal-{personal_event_id}",
                    owner_user_id="rls-user-a",
                    team_id=None,
                )
                _insert_event(
                    connection,
                    event_id=team_event_id,
                    stream_id=f"team-{team_event_id}",
                    owner_user_id=None,
                    team_id="rls-team-a",
                )

            with connection.begin():
                connection.execute(text("SET LOCAL ROLE opencitadel_execution_kernel"))
                _set_context(connection, mode="user", user_id="rls-user-a")
                visible = (
                    connection.execute(
                        text(
                            "SELECT event_id FROM execution_events "
                            "WHERE event_id IN (:personal, :team)"
                        ),
                        {"personal": personal_event_id, "team": team_event_id},
                    )
                    .scalars()
                    .all()
                )
                assert visible == [personal_event_id]

            with connection.begin():
                connection.execute(text("SET LOCAL ROLE opencitadel_execution_kernel"))
                _set_context(
                    connection,
                    mode="user",
                    user_id="rls-user-b",
                    team_id="rls-team-a",
                )
                visible = (
                    connection.execute(
                        text(
                            "SELECT event_id FROM execution_events "
                            "WHERE event_id IN (:personal, :team)"
                        ),
                        {"personal": personal_event_id, "team": team_event_id},
                    )
                    .scalars()
                    .all()
                )
                assert visible == [team_event_id]

            with connection.begin():
                connection.execute(text("SET LOCAL ROLE opencitadel_execution_kernel"))
                _set_context(connection, mode="system")
                assert (
                    connection.execute(
                        text(
                            "SELECT count(*) FROM execution_events "
                            "WHERE event_id IN (:personal, :team)"
                        ),
                        {"personal": personal_event_id, "team": team_event_id},
                    ).scalar_one()
                    == 2
                )

            with connection.begin():
                connection.execute(text("SET LOCAL ROLE opencitadel_execution_kernel"))
                _set_context(
                    connection,
                    mode="user",
                    user_id="rls-admin",
                    is_admin=True,
                )
                assert (
                    connection.execute(
                        text(
                            "SELECT count(*) FROM execution_events "
                            "WHERE event_id IN (:personal, :team)"
                        ),
                        {"personal": personal_event_id, "team": team_event_id},
                    ).scalar_one()
                    == 2
                )
    finally:
        engine.dispose()
        _cleanup_scopes(
            owner_user_ids=("rls-user-a",),
            team_ids=("rls-team-a",),
        )


@pytest.mark.usefixtures("_db_schema")
def test_cross_tenant_insert_and_event_mutation_are_denied() -> None:
    engine = create_engine(execution_kernel_database_uri(async_driver=False))
    try:
        with engine.connect() as connection:

            def insert_cross_tenant_event():
                _insert_event(
                    connection,
                    event_id=uuid4(),
                    stream_id=f"cross-{uuid4()}",
                    owner_user_id="rls-user-b",
                    team_id=None,
                )

            with pytest.raises(DBAPIError):
                _execute_as_kernel(
                    connection,
                    mode="user",
                    user_id="rls-user-a",
                    operation=insert_cross_tenant_event,
                )
            connection.rollback()

            with connection.begin():
                _set_context(connection, mode="system")
                event_id = uuid4()
                _insert_event(
                    connection,
                    event_id=event_id,
                    stream_id=f"immutable-{event_id}",
                    owner_user_id="rls-user-a",
                    team_id=None,
                )

            for statement in (
                "UPDATE execution_events SET public_payload = '{}'::jsonb WHERE event_id = :event_id",
                "DELETE FROM execution_events WHERE event_id = :event_id",
            ):

                def mutate_event(statement=statement):
                    connection.execute(text(statement), {"event_id": event_id})

                with pytest.raises(DBAPIError):
                    _execute_as_kernel(
                        connection,
                        mode="system",
                        operation=mutate_event,
                    )
                connection.rollback()
    finally:
        engine.dispose()
        _cleanup_scopes(owner_user_ids=("rls-user-a",))


@pytest.mark.usefixtures("_db_schema")
def test_all_mutable_execution_tables_enforce_personal_and_team_rls() -> None:
    engine = create_engine(execution_kernel_database_uri(async_driver=False))
    marker = f"rls-six-{uuid4()}"
    personal_user_id = f"{marker}-user"
    team_id = f"{marker}-team"
    try:
        with engine.connect() as connection:
            with connection.begin():
                _set_context(connection, mode="system")
                personal_position = _insert_event(
                    connection,
                    event_id=uuid4(),
                    stream_id=f"{marker}-personal-event",
                    owner_user_id=personal_user_id,
                    team_id=None,
                )
                team_position = _insert_event(
                    connection,
                    event_id=uuid4(),
                    stream_id=f"{marker}-team-event",
                    owner_user_id=None,
                    team_id=team_id,
                )
                _insert_mutable_execution_rows(
                    connection,
                    marker=marker,
                    personal_position=personal_position,
                    team_position=team_position,
                    personal_user_id=personal_user_id,
                    team_id=team_id,
                )

            mutable_tables = MUTABLE_EXECUTION_TABLES
            with connection.begin():
                connection.execute(text("SET LOCAL ROLE opencitadel_execution_kernel"))
                _set_context(
                    connection,
                    mode="user",
                    user_id=personal_user_id,
                )
                for table in mutable_tables:
                    assert (
                        connection.execute(
                            text(
                                f"SELECT count(*) FROM {table} "
                                "WHERE owner_user_id = :owner_user_id "
                                "OR team_id = :team_id"
                            ),
                            {
                                "owner_user_id": personal_user_id,
                                "team_id": team_id,
                            },
                        ).scalar_one()
                        == 1
                    )

            with connection.begin():
                connection.execute(text("SET LOCAL ROLE opencitadel_execution_kernel"))
                _set_context(
                    connection,
                    mode="user",
                    user_id=f"{marker}-team-member",
                    team_id=team_id,
                )
                for table in mutable_tables:
                    assert (
                        connection.execute(
                            text(
                                f"SELECT count(*) FROM {table} "
                                "WHERE owner_user_id = :owner_user_id "
                                "OR team_id = :team_id"
                            ),
                            {
                                "owner_user_id": personal_user_id,
                                "team_id": team_id,
                            },
                        ).scalar_one()
                        == 1
                    )
    finally:
        engine.dispose()
        _cleanup_scopes(
            owner_user_ids=(personal_user_id,),
            team_ids=(team_id,),
        )


@pytest.mark.usefixtures("_db_schema")
def test_append_role_can_insert_but_has_no_event_mutation_privileges() -> None:
    engine = create_engine(execution_kernel_database_uri(async_driver=False))
    event_id = uuid4()
    try:
        with engine.connect() as connection:
            connection.rollback()
            acl = connection.execute(
                text(
                    """
                    SELECT
                      has_table_privilege(
                        'opencitadel_execution_kernel',
                        'execution_events', 'INSERT'
                      ),
                      has_table_privilege(
                        'opencitadel_execution_kernel',
                        'execution_events', 'UPDATE'
                      ),
                      has_table_privilege(
                        'opencitadel_execution_kernel',
                        'execution_events', 'DELETE'
                      )
                    """
                )
            ).one()
            assert acl == (True, False, False)
            connection.rollback()

            with connection.begin():
                connection.execute(text("SET LOCAL ROLE opencitadel_execution_kernel"))
                _set_context(connection, mode="system")
                _insert_event(
                    connection,
                    event_id=event_id,
                    stream_id=f"append-role-{event_id}",
                    owner_user_id="append-role-user",
                    team_id=None,
                )

            for statement in (
                (
                    "UPDATE execution_events SET public_payload = '{}'::jsonb "
                    "WHERE event_id = :event_id"
                ),
                "DELETE FROM execution_events WHERE event_id = :event_id",
            ):

                def mutate_event(statement=statement):
                    connection.execute(text(statement), {"event_id": event_id})

                with pytest.raises(DBAPIError):
                    _execute_as_kernel(
                        connection,
                        mode="system",
                        operation=mutate_event,
                    )
                connection.rollback()
    finally:
        engine.dispose()
        _cleanup_scopes(owner_user_ids=("append-role-user",))
