"""Provision distinct migration, API, and execution-kernel roles for CI."""

from __future__ import annotations

import os

import psycopg2
from psycopg2 import sql


def _required(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} must be set")
    return value


def _ensure_group(cursor, name: str) -> None:
    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,))
    if cursor.fetchone() is None:
        cursor.execute(
            sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(name))
        )
    cursor.execute(
        sql.SQL("ALTER ROLE {} WITH NOLOGIN NOSUPERUSER NOBYPASSRLS").format(sql.Identifier(name))
    )


def _ensure_login(cursor, name: str, password: str, *, inherit: bool) -> None:
    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,))
    if cursor.fetchone() is None:
        cursor.execute(
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s NOSUPERUSER NOBYPASSRLS").format(
                sql.Identifier(name)
            ),
            (password,),
        )
    inheritance = sql.SQL("INHERIT") if inherit else sql.SQL("NOINHERIT")
    cursor.execute(
        sql.SQL("ALTER ROLE {} WITH LOGIN {} PASSWORD %s NOSUPERUSER NOBYPASSRLS").format(
            sql.Identifier(name), inheritance
        ),
        (password,),
    )


def _clear_memberships(cursor, name: str) -> None:
    cursor.execute(
        "SELECT granted.rolname FROM pg_auth_members AS membership "
        "JOIN pg_roles AS granted ON granted.oid = membership.roleid "
        "JOIN pg_roles AS recipient ON recipient.oid = membership.member "
        "WHERE recipient.rolname = %s",
        (name,),
    )
    for (granted,) in cursor.fetchall():
        cursor.execute(
            sql.SQL("REVOKE {} FROM {}").format(sql.Identifier(granted), sql.Identifier(name))
        )


def main() -> None:
    host = os.environ.get("POSTGRES_HOST", "localhost")
    database = os.environ.get("POSTGRES_DB", "opencitadel")
    admin_password = _required("POSTGRES_ADMIN_PASSWORD")
    migration_user = _required("POSTGRES_MIGRATION_USER")
    migration_password = _required("POSTGRES_MIGRATION_PASSWORD")
    app_user = _required("POSTGRES_APP_USER")
    app_password = _required("POSTGRES_APP_PASSWORD")
    kernel_user = _required("POSTGRES_KERNEL_USER")
    kernel_password = _required("POSTGRES_KERNEL_PASSWORD")

    connection = psycopg2.connect(
        host=host,
        dbname=database,
        user="postgres",
        password=admin_password,
    )
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cursor.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            _ensure_group(cursor, "opencitadel_execution_api")
            _ensure_group(cursor, "opencitadel_execution_kernel")
            _ensure_login(cursor, migration_user, migration_password, inherit=False)
            _ensure_login(cursor, app_user, app_password, inherit=True)
            _ensure_login(cursor, kernel_user, kernel_password, inherit=True)
            for role in (migration_user, app_user, kernel_user):
                _clear_memberships(cursor, role)
                cursor.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                        sql.Identifier(database), sql.Identifier(role)
                    )
                )
                cursor.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role))
                )
            cursor.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {} WITH GRANT OPTION").format(
                    sql.Identifier(migration_user)
                )
            )
            cursor.execute(
                sql.SQL("GRANT CREATE ON SCHEMA public TO {}").format(
                    sql.Identifier(migration_user)
                )
            )
            cursor.execute(
                sql.SQL("GRANT opencitadel_execution_api TO {}").format(sql.Identifier(app_user))
            )
            cursor.execute(
                sql.SQL("GRANT opencitadel_execution_kernel TO {}").format(
                    sql.Identifier(kernel_user)
                )
            )
    finally:
        connection.close()


if __name__ == "__main__":
    main()
