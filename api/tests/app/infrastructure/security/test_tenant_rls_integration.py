#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Database-backed proof that the application role cannot bypass tenant RLS."""
from sqlalchemy import create_engine, text

from core.config import get_settings, sqlalchemy_sync_database_uri


def test_application_role_is_non_bypass_and_rls_is_effective(_db_schema):
    engine = create_engine(sqlalchemy_sync_database_uri())
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
            assert role[0] == get_settings().postgres_user
            assert role[1] is False
            assert role[2] is False

            rls = connection.execute(
                text(
                    """
                    SELECT relrowsecurity, relforcerowsecurity
                    FROM pg_class
                    WHERE oid = 'app_configs'::regclass
                    """
                )
            ).one()
            assert rls == (True, True)

            anonymous_count = connection.execute(
                text("SELECT count(*) FROM app_configs")
            ).scalar_one()
            assert anonymous_count == 0
            connection.rollback()

            with connection.begin():
                connection.execute(
                    text("SELECT set_config('app.auth_mode', 'user', true)")
                )
                connection.execute(
                    text("SELECT set_config('app.user_id', 'rls-test-user', true)")
                )
                visible_count = connection.execute(
                    text("SELECT count(*) FROM app_configs WHERE scope = 'global'")
                ).scalar_one()
                assert visible_count == 1

                mutated_global_rows = connection.execute(
                    text(
                        """
                        UPDATE app_configs
                        SET payload = payload
                        WHERE scope = 'global'
                        RETURNING id
                        """
                    )
                ).all()
                assert mutated_global_rows == []
    finally:
        engine.dispose()
