"""Create the destructive greenfield OpenCitadel v2 schema.

Revision ID: 0001greenfield
Revises: None
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from app.domain.runtime_policy.governance import GovernancePolicy
from app.infrastructure.models.registry import model_metadata
from app.infrastructure.security.tenant_rls import (
    RLS_TABLES,
    apply_row_level_security,
    disable_policy_statements,
)

revision: str = "0001greenfield"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _execute(statements: str | list[str]) -> None:
    for statement in [statements] if isinstance(statements, str) else statements:
        op.execute(sa.text(statement))


def _install_extensions() -> None:
    _execute(
        [
            "CREATE EXTENSION IF NOT EXISTS pgcrypto",
            "CREATE EXTENSION IF NOT EXISTS vector",
        ]
    )


def _install_runtime_roles() -> None:
    _execute(
        """
        DO $$
        DECLARE
            api_role text := current_setting('app.runtime_database_role', true);
            kernel_role text := current_setting('app.execution_runtime_role', true);
        BEGIN
            IF api_role IS NULL OR api_role = '' OR kernel_role IS NULL OR kernel_role = '' THEN
                RAISE EXCEPTION 'runtime database role settings are required';
            END IF;
            IF api_role = kernel_role THEN
                RAISE EXCEPTION 'API and kernel database roles must differ';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = api_role) THEN
                EXECUTE format('CREATE ROLE %I NOLOGIN NOBYPASSRLS', api_role);
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = kernel_role) THEN
                EXECUTE format('CREATE ROLE %I NOLOGIN NOBYPASSRLS', kernel_role);
            END IF;
        END;
        $$
        """
    )


def _install_authorization_guard() -> None:
    _execute(
        [
            """
            CREATE TABLE kernel_authorization_secrets (
                singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
                signing_secret text NOT NULL CHECK (signing_secret <> '')
            )
            """,
            """
            INSERT INTO kernel_authorization_secrets (singleton, signing_secret)
            VALUES (true, current_setting('app.rls_signing_secret'))
            """,
            "REVOKE ALL ON kernel_authorization_secrets FROM PUBLIC",
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
                    current_setting('app.auth_signature', true), ''
                );
            BEGIN
                SELECT signing_secret INTO secret
                FROM public.kernel_authorization_secrets WHERE singleton = true;
                IF secret IS NULL OR length(provided_signature) <> 64 THEN
                    RETURN false;
                END IF;
                claims := concat(
                    COALESCE(current_setting('app.auth_mode', true), ''), chr(31),
                    COALESCE(current_setting('app.user_id', true), ''), chr(31),
                    COALESCE(current_setting('app.team_id', true), ''), chr(31),
                    COALESCE(current_setting('app.is_admin', true), ''), chr(31),
                    COALESCE(current_setting('app.request_id', true), ''), chr(31),
                    COALESCE(current_setting('app.system_actor', true), ''), chr(31),
                    COALESCE(current_setting('app.is_auditor', true), '')
                );
                expected_signature := encode(
                    public.hmac(convert_to(claims, 'UTF8'), convert_to(secret, 'UTF8'), 'sha256'),
                    'hex'
                );
                RETURN expected_signature = provided_signature;
            END;
            $$
            """,
        ]
    )


def _seed_governance_policy() -> None:
    policy = GovernancePolicy()
    revision_id = UUID("00000000-0000-0000-0000-000000000001")
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            INSERT INTO governance_policy_revisions
                (id, policy, digest, actor_user_id, note, created_at)
            VALUES (:id, CAST(:policy AS jsonb), :digest, 'migration', 'greenfield default', now())
            """
        ),
        {
            "id": revision_id,
            "policy": __import__("json").dumps(policy.model_dump(mode="json")),
            "digest": policy.digest,
        },
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO governance_policy_head (id, revision_id, generation, updated_at)
            VALUES (1, :revision_id, 1, now())
            """
        ),
        {"revision_id": revision_id},
    )


def _install_immutability_guards() -> None:
    _execute(
        [
            """
            CREATE FUNCTION opencitadel_reject_immutable_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = '55000';
            END;
            $$
            """,
            """
            CREATE FUNCTION opencitadel_guard_kernel_event_mutation()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'DELETE'
                   AND public.opencitadel_authorization_valid()
                   AND current_setting('app.auth_mode', true) = 'system'
                   AND current_setting('app.system_actor', true) = 'kernel-purge' THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = '55000';
            END;
            $$
            """,
            """
            CREATE TRIGGER kernel_events_immutable BEFORE UPDATE OR DELETE ON kernel_events
            FOR EACH ROW EXECUTE FUNCTION opencitadel_guard_kernel_event_mutation()
            """,
            """
            CREATE TRIGGER audit_records_immutable BEFORE UPDATE OR DELETE ON audit_records
            FOR EACH ROW EXECUTE FUNCTION opencitadel_reject_immutable_mutation()
            """,
            """
            CREATE TRIGGER governance_revisions_immutable
            BEFORE UPDATE OR DELETE ON governance_policy_revisions
            FOR EACH ROW EXECUTE FUNCTION opencitadel_reject_immutable_mutation()
            """,
            """
            CREATE FUNCTION opencitadel_reject_owner_scope_change()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF OLD.owner_user_id IS DISTINCT FROM NEW.owner_user_id
                   OR OLD.team_id IS DISTINCT FROM NEW.team_id THEN
                    RAISE EXCEPTION 'owner scope is immutable' USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END;
            $$
            """,
        ]
    )
    owner_tables = sorted(
        table
        for table in RLS_TABLES
        if {"owner_user_id", "team_id"} <= set(model_metadata.tables[table].c.keys())
    )
    for table in owner_tables:
        _execute(
            f"CREATE TRIGGER {table}_owner_immutable BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION opencitadel_reject_owner_scope_change()"
        )


def _grant_runtime_privileges() -> None:
    _execute(
        """
        DO $$
        DECLARE
            api_role text := current_setting('app.runtime_database_role', true);
            kernel_role text := current_setting('app.execution_runtime_role', true);
        BEGIN
            EXECUTE format('GRANT USAGE ON SCHEMA public TO %I, %I', api_role, kernel_role);
            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I, %I',
                api_role, kernel_role
            );
            EXECUTE format(
                'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO %I, %I',
                api_role, kernel_role
            );
            EXECUTE format(
                'REVOKE UPDATE ON kernel_events FROM %I, %I', api_role, kernel_role
            );
            EXECUTE format(
                'REVOKE UPDATE, DELETE ON audit_records, governance_policy_revisions '
                'FROM %I, %I', api_role, kernel_role
            );
            EXECUTE format(
                'REVOKE INSERT, UPDATE, DELETE ON kernel_effects, kernel_timers, '
                'kernel_outbox FROM %I', api_role
            );
        END;
        $$
        """
    )


def upgrade() -> None:
    _install_extensions()
    _install_runtime_roles()
    model_metadata.create_all(bind=op.get_bind(), checkfirst=False)
    _install_authorization_guard()
    _seed_governance_policy()
    apply_row_level_security(_execute)
    _install_immutability_guards()
    _grant_runtime_privileges()


def downgrade() -> None:
    for table in sorted(RLS_TABLES):
        _execute(disable_policy_statements(table))
    model_metadata.drop_all(bind=op.get_bind(), checkfirst=True)
    _execute(
        [
            "DROP FUNCTION IF EXISTS opencitadel_reject_owner_scope_change() CASCADE",
            "DROP FUNCTION IF EXISTS opencitadel_guard_kernel_event_mutation() CASCADE",
            "DROP FUNCTION IF EXISTS opencitadel_reject_immutable_mutation() CASCADE",
            "DROP FUNCTION IF EXISTS opencitadel_authorization_valid()",
            "DROP TABLE IF EXISTS kernel_authorization_secrets",
        ]
    )
