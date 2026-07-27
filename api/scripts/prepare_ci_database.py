#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Prepare the CI database so RLS tests run as a non-bypass application role."""
from __future__ import annotations

import os

import psycopg2
from psycopg2 import sql


def _required(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} must be set")
    return value


def main() -> None:
    host = os.environ.get("POSTGRES_HOST", "localhost")
    database = os.environ.get("POSTGRES_DB", "opencitadel")
    admin_password = _required("POSTGRES_ADMIN_PASSWORD")
    app_user = _required("POSTGRES_APP_USER")
    app_password = _required("POSTGRES_APP_PASSWORD")

    connection = psycopg2.connect(
        host=host,
        dbname=database,
        user="postgres",
        password=admin_password,
    )
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_roles WHERE rolname = %s",
                (app_user,),
            )
            if cursor.fetchone() is None:
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD %s "
                        "NOSUPERUSER NOBYPASSRLS"
                    ).format(sql.Identifier(app_user)),
                    (app_password,),
                )
            cursor.execute(
                sql.SQL(
                    "ALTER ROLE {} WITH LOGIN PASSWORD %s "
                    "NOSUPERUSER NOBYPASSRLS"
                ).format(sql.Identifier(app_user)),
                (app_password,),
            )
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cursor.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
            cursor.execute(
                sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                    sql.Identifier(database),
                    sql.Identifier(app_user),
                )
            )
            cursor.execute(
                sql.SQL("ALTER SCHEMA public OWNER TO {}").format(
                    sql.Identifier(app_user)
                )
            )
            cursor.execute(
                sql.SQL("GRANT ALL ON SCHEMA public TO {}").format(
                    sql.Identifier(app_user)
                )
            )
    finally:
        connection.close()


if __name__ == "__main__":
    main()
