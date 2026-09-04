"""Create the complete greenfield OpenCitadel schema.

Revision ID: 0001greenfield
Revises: None
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.infrastructure.models.registry import model_metadata
from app.infrastructure.security.tenant_rls import (
    EXECUTION_ROOT_TABLES,
    apply_row_level_security,
)

revision: str = "0001greenfield"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


FORMAL_PROJECTIONS = {
    "execution_activity_projection",
    "execution_approval_projection",
    "execution_public_events",
    "execution_resource_build_projection",
    "execution_run_projection",
}
APPEND_ONLY_EXECUTION_TABLES = {
    "execution_events",
    "execution_stream_owners",
}
MUTABLE_EXECUTION_TABLES = EXECUTION_ROOT_TABLES - APPEND_ONLY_EXECUTION_TABLES


def _execute(statements: str | list[str]) -> None:
    if isinstance(statements, str):
        statements = [statements]
    for statement in statements:
        op.execute(sa.text(statement))


def _assert_runtime_roles() -> None:
    _execute(
        """
        DO $$
        DECLARE
            api_role text := current_setting('app.runtime_database_role', true);
            kernel_role text := current_setting(
                'app.execution_runtime_role', true
            );
            signing_secret text := current_setting(
                'app.rls_signing_secret', true
            );
        BEGIN
            IF api_role IS NULL OR api_role = '' THEN
                RAISE EXCEPTION 'app.runtime_database_role is required';
            END IF;
            IF kernel_role IS NULL OR kernel_role = '' THEN
                RAISE EXCEPTION 'app.execution_runtime_role is required';
            END IF;
            IF api_role = kernel_role THEN
                RAISE EXCEPTION 'API and execution-kernel roles must differ';
            END IF;
            IF signing_secret IS NULL OR signing_secret = '' THEN
                RAISE EXCEPTION 'app.rls_signing_secret is required';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role) THEN
                RAISE EXCEPTION 'API database role % does not exist', api_role;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_roles WHERE rolname = kernel_role
            ) THEN
                RAISE EXCEPTION 'execution-kernel role % does not exist',
                    kernel_role;
            END IF;
        END;
        $$
        """
    )


def _install_extensions() -> None:
    _execute(
        [
            'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"',
            "CREATE EXTENSION IF NOT EXISTS pgcrypto",
            "CREATE EXTENSION IF NOT EXISTS vector",
            # 会话搜索按 ILIKE '%q%' 过滤 title/latest_message；启用 pg_trgm
            # 以便后续为 sessions(title/latest_message) 建 GIN trgm 索引加速
            # 子串检索（当前查询已正确，索引为纯性能优化，随模型改动落地）。
            "CREATE EXTENSION IF NOT EXISTS pg_trgm",
        ]
    )


def _create_product_schema() -> None:
    model_metadata.create_all(bind=op.get_bind(), checkfirst=False)


def _install_authorization_guard() -> None:
    _execute(
        [
            """
            CREATE TABLE execution_authorization_secrets (
                singleton boolean PRIMARY KEY DEFAULT true
                    CHECK (singleton),
                signing_secret text NOT NULL CHECK (signing_secret <> '')
            )
            """,
            """
            INSERT INTO execution_authorization_secrets (
                singleton,
                signing_secret
            )
            VALUES (
                true,
                current_setting('app.rls_signing_secret')
            )
            """,
            "REVOKE ALL ON execution_authorization_secrets FROM PUBLIC",
            """
            CREATE FUNCTION opencitadel_authorization_valid()
            RETURNS boolean
            LANGUAGE plpgsql
            STABLE
            SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $$
            DECLARE
                secret text;
                claims text;
                expected_signature text;
                provided_signature text := COALESCE(
                    current_setting('app.auth_signature', true),
                    ''
                );
            BEGIN
                SELECT signing_secret
                INTO secret
                FROM public.execution_authorization_secrets
                WHERE singleton = true;
                IF secret IS NULL OR length(provided_signature) <> 64 THEN
                    RETURN false;
                END IF;
                claims := concat(
                    COALESCE(current_setting('app.auth_mode', true), ''),
                    chr(31),
                    COALESCE(current_setting('app.user_id', true), ''),
                    chr(31),
                    COALESCE(current_setting('app.team_id', true), ''),
                    chr(31),
                    COALESCE(current_setting('app.is_admin', true), ''),
                    chr(31),
                    COALESCE(current_setting('app.request_id', true), ''),
                    chr(31),
                    COALESCE(current_setting('app.system_actor', true), ''),
                    chr(31),
                    COALESCE(current_setting('app.is_auditor', true), '')
                );
                expected_signature := encode(
                    public.hmac(
                        convert_to(claims, 'UTF8'),
                        convert_to(secret, 'UTF8'),
                        'sha256'
                    ),
                    'hex'
                );
                RETURN expected_signature = provided_signature;
            END;
            $$
            """,
        ]
    )


def _apply_row_level_security() -> None:
    # Single canonical policy matrix (auditor read-all / no-write baked in).
    apply_row_level_security(_execute)


def _install_append_only_guards() -> None:
    _execute(
        [
            """
            CREATE FUNCTION opencitadel_reject_execution_event_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'execution_events are immutable'
                    USING ERRCODE = '55000';
            END;
            $$
            """,
            """
            CREATE TRIGGER execution_events_immutable
            BEFORE UPDATE OR DELETE ON execution_events
            FOR EACH ROW
            EXECUTE FUNCTION opencitadel_reject_execution_event_mutation()
            """,
            """
            CREATE TRIGGER execution_stream_owners_immutable
            BEFORE UPDATE OR DELETE ON execution_stream_owners
            FOR EACH ROW
            EXECUTE FUNCTION opencitadel_reject_execution_event_mutation()
            """,
            """
            CREATE FUNCTION opencitadel_reject_audit_log_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'audit_logs are append-only'
                    USING ERRCODE = '55000';
            END;
            $$
            """,
            """
            CREATE TRIGGER audit_logs_immutable
            BEFORE UPDATE OR DELETE ON audit_logs
            FOR EACH ROW
            EXECUTE FUNCTION opencitadel_reject_audit_log_mutation()
            """,
            """
            CREATE FUNCTION opencitadel_reject_runtime_policy_revision_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'runtime policy revisions are immutable'
                    USING ERRCODE = '55000';
            END;
            $$
            """,
            """
            CREATE TRIGGER execution_policy_revisions_immutable
            BEFORE UPDATE OR DELETE ON execution_policy_revisions
            FOR EACH ROW
            EXECUTE FUNCTION opencitadel_reject_runtime_policy_revision_mutation()
            """,
            """
            CREATE TRIGGER operations_policy_revisions_immutable
            BEFORE UPDATE OR DELETE ON operations_policy_revisions
            FOR EACH ROW
            EXECUTE FUNCTION opencitadel_reject_runtime_policy_revision_mutation()
            """,
            (
                "REVOKE UPDATE, DELETE ON execution_events, "
                "execution_stream_owners, audit_logs, "
                "execution_policy_revisions, operations_policy_revisions FROM PUBLIC"
            ),
        ]
    )


def _grant_runtime_privileges() -> None:
    execution_mutable = ", ".join(sorted(MUTABLE_EXECUTION_TABLES))
    projections = ", ".join(sorted(FORMAL_PROJECTIONS))
    _execute(
        f"""
        DO $$
        DECLARE
            api_role text := current_setting('app.runtime_database_role', true);
            kernel_role text := current_setting(
                'app.execution_runtime_role', true
            );
            product_tables text;
            product_sequences text;
        BEGIN
            EXECUTE format(
                'GRANT USAGE ON SCHEMA public TO %I, %I',
                api_role,
                kernel_role
            );

            SELECT string_agg(format('%I.%I', n.nspname, c.relname), ', ')
            INTO product_tables
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relkind IN ('r', 'p')
              AND c.relname <> 'alembic_version'
              AND left(c.relname, 10) <> 'execution_';

            IF product_tables IS NOT NULL THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE '
                    || product_tables
                    || format(' TO %I, %I', api_role, kernel_role);
            END IF;

            SELECT string_agg(format('%I.%I', n.nspname, c.relname), ', ')
            INTO product_sequences
            FROM pg_class AS c
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relkind = 'S'
              AND left(c.relname, 10) <> 'execution_';

            IF product_sequences IS NOT NULL THEN
                EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE '
                    || product_sequences
                    || format(' TO %I, %I', api_role, kernel_role);
            END IF;

            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM %I',
                kernel_role
            );
            IF product_tables IS NOT NULL THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE '
                    || product_tables || format(' TO %I', kernel_role);
            END IF;
            IF product_sequences IS NOT NULL THEN
                EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE '
                    || product_sequences || format(' TO %I', kernel_role);
            END IF;
            EXECUTE format(
                'GRANT SELECT, INSERT ON execution_events, '
                'execution_stream_owners TO %I', kernel_role
            );
            EXECUTE format(
                'GRANT USAGE, SELECT ON SEQUENCE '
                'execution_events_position_seq TO %I', kernel_role
            );
            -- Public feed rows now carry their own identity sequence; both the
            -- formal projector and the activity progress sink insert as the
            -- kernel role.
            EXECUTE format(
                'GRANT USAGE, SELECT ON SEQUENCE '
                'execution_public_events_seq_seq TO %I', kernel_role
            );
            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE, DELETE ON {execution_mutable} '
                'TO %I', kernel_role
            );
            -- Kernel-internal Run quarantine (no tenant RLS); kernel-only.
            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE ON execution_poisoned_runs '
                'TO %I', kernel_role
            );
            -- Kernel-internal per-scope head watermark (no tenant RLS);
            -- kernel-only. Upserted by the append path, read by projector
            -- scope discovery (list_pending).
            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE ON execution_scope_head '
                'TO %I', kernel_role
            );
            -- Kernel-internal owner-scope quarantine + rebuild marker (K4-1,
            -- no tenant RLS). The kernel writes/clears rows; the rebuild CLI
            -- (kernel credentials) deletes them on completion.
            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE, DELETE ON '
                'execution_poisoned_scopes TO %I', kernel_role
            );

            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM %I',
                api_role
            );
            EXECUTE format(
                'GRANT SELECT ON alembic_version TO %I', api_role
            );
            IF product_tables IS NOT NULL THEN
                EXECUTE 'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE '
                    || product_tables || format(' TO %I', api_role);
            END IF;
            IF product_sequences IS NOT NULL THEN
                EXECUTE 'GRANT USAGE, SELECT ON SEQUENCE '
                    || product_sequences || format(' TO %I', api_role);
            END IF;
            EXECUTE format(
                'GRANT SELECT, INSERT ON execution_command_inbox TO %I',
                api_role
            );
            EXECUTE format(
                'GRANT SELECT ON {projections} TO %I', api_role
            );
            -- Admin observability (K4-3): the API status endpoint reads
            -- per-scope projection lag (scope head vs formal checkpoint) and
            -- the quarantined-scope list. Read-only; checkpoints stay under
            -- RLS (system/admin), the two control tables carry no tenant RLS.
            EXECUTE format(
                'GRANT SELECT ON execution_projector_checkpoints, '
                'execution_scope_head, execution_poisoned_scopes TO %I',
                api_role
            );

            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON execution_policy_revisions, '
                'operations_policy_revisions, runtime_policy_heads FROM %I, %I',
                api_role,
                kernel_role
            );
            EXECUTE format(
                'REVOKE ALL PRIVILEGES ON SEQUENCE '
                'execution_policy_revisions_sequence_seq, '
                'operations_policy_revisions_sequence_seq FROM %I, %I',
                api_role,
                kernel_role
            );
            EXECUTE format(
                'GRANT SELECT, INSERT ON execution_policy_revisions, '
                'operations_policy_revisions TO %I', api_role
            );
            EXECUTE format(
                'GRANT SELECT, UPDATE ON runtime_policy_heads TO %I',
                api_role
            );
            EXECUTE format(
                'GRANT USAGE, SELECT ON SEQUENCE '
                'execution_policy_revisions_sequence_seq, '
                'operations_policy_revisions_sequence_seq TO %I',
                api_role
            );
            EXECUTE format(
                'GRANT SELECT ON execution_policy_revisions, '
                'operations_policy_revisions, runtime_policy_heads TO %I',
                kernel_role
            );
        END;
        $$
        """
    )


def upgrade() -> None:
    _assert_runtime_roles()
    _install_extensions()
    _create_product_schema()
    _install_authorization_guard()
    _apply_row_level_security()
    _install_append_only_guards()
    _grant_runtime_privileges()


def downgrade() -> None:
    _execute(
        [
            ("DROP TRIGGER IF EXISTS execution_events_immutable ON execution_events"),
            ("DROP TRIGGER IF EXISTS execution_stream_owners_immutable ON execution_stream_owners"),
            ("DROP FUNCTION IF EXISTS opencitadel_reject_execution_event_mutation()"),
            "DROP TRIGGER IF EXISTS audit_logs_immutable ON audit_logs",
            "DROP FUNCTION IF EXISTS opencitadel_reject_audit_log_mutation()",
            (
                "DROP TRIGGER IF EXISTS execution_policy_revisions_immutable "
                "ON execution_policy_revisions"
            ),
            (
                "DROP TRIGGER IF EXISTS operations_policy_revisions_immutable "
                "ON operations_policy_revisions"
            ),
            "DROP FUNCTION IF EXISTS opencitadel_reject_runtime_policy_revision_mutation()",
        ]
    )
    model_metadata.drop_all(bind=op.get_bind(), checkfirst=False)
    _execute(
        [
            "DROP FUNCTION IF EXISTS opencitadel_authorization_valid()",
            "DROP TABLE IF EXISTS execution_authorization_secrets",
        ]
    )
