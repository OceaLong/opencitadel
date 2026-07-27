#!/usr/bin/env python
# -*- coding: utf-8 -*-
from pathlib import Path

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

    configure_sync_system_authorization(connection, actor="alembic-migration")

    assert len(connection.calls) == 1
    statement, parameters = connection.calls[0]
    assert "set_config('app.auth_mode', :auth_mode, true)" in statement
    assert "set_config('app.system_actor', :system_actor, true)" in statement
    assert parameters == {
        "auth_mode": "system",
        "user_id": "",
        "team_id": "",
        "is_admin": "false",
        "request_id": "",
        "system_actor": "alembic-migration",
    }


def test_alembic_online_runner_binds_system_authorization_before_migrations():
    env_source = (
        Path(__file__).parents[4] / "alembic" / "env.py"
    ).read_text(encoding="utf-8")

    configure_index = env_source.index("configure_sync_system_authorization(")
    migrate_index = env_source.index("context.run_migrations()", configure_index)

    assert configure_index < migrate_index
