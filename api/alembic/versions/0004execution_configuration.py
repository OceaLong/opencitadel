"""Immutable resolved configuration and pre-send physical accounting intents."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0004execution_configuration"
down_revision = "0003resource_pins"
branch_labels = None
depends_on = None

_SCOPE = """owner_user_id varchar(255), team_id varchar(255),
 scope_key varchar(261) GENERATED ALWAYS AS (CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END) STORED,
 created_by varchar(255) NOT NULL, created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP, schema_version integer NOT NULL DEFAULT 1 CHECK(schema_version>0),
 CHECK ((owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL))"""
TABLES = {
    "execution_usage_delivery": f"""call_identity varchar(255) NOT NULL, phase varchar(16) NOT NULL CHECK(phase IN ('dispatch','settlement')),
 failures integer NOT NULL CHECK(failures>0), next_attempt_at timestamptz NOT NULL, quarantined boolean NOT NULL DEFAULT false,
 last_error_code varchar(64) NOT NULL, {_SCOPE}, PRIMARY KEY(scope_key,call_identity,phase),
 FOREIGN KEY(scope_key,call_identity) REFERENCES execution_model_dispatches(scope_key,call_identity) ON DELETE RESTRICT""",
    "execution_usage_publications": f"""call_identity varchar(255) NOT NULL, phase varchar(16) NOT NULL CHECK(phase IN ('dispatch','settlement')),
 event_id uuid NOT NULL, event_position bigint NOT NULL CHECK(event_position>0), {_SCOPE}, PRIMARY KEY(scope_key,call_identity,phase),
 FOREIGN KEY(scope_key,call_identity) REFERENCES execution_model_dispatches(scope_key,call_identity) ON DELETE RESTRICT""",
    "execution_configurations": f"""id varchar(64) NOT NULL, run_id uuid NOT NULL, body jsonb NOT NULL,
 purpose varchar(32) NOT NULL CHECK(purpose IN ('production','evaluation_subject','evaluation_judge','unknown')),
 {_SCOPE}, PRIMARY KEY(scope_key,id), UNIQUE(scope_key,run_id,id)""",
    "execution_model_dispatches": f"""call_identity varchar(255) NOT NULL, run_id uuid NOT NULL, activity_id uuid NOT NULL,
 generation integer NOT NULL CHECK(generation>=0), claim_generation integer NOT NULL CHECK(claim_generation>0),
 attempt_id varchar(255) NOT NULL, logical_group varchar(64) NOT NULL, ordinal integer NOT NULL CHECK(ordinal>0),
 configuration_id varchar(64) NOT NULL, request_snapshot jsonb NOT NULL,
 {_SCOPE}, PRIMARY KEY(scope_key,call_identity), UNIQUE(scope_key,attempt_id,logical_group,ordinal),
 FOREIGN KEY(scope_key,run_id,configuration_id) REFERENCES execution_configurations(scope_key,run_id,id) ON DELETE RESTRICT""",
    "execution_model_settlements": f"""call_identity varchar(255) NOT NULL, fact jsonb NOT NULL, {_SCOPE},
 PRIMARY KEY(scope_key,call_identity), FOREIGN KEY(scope_key,call_identity) REFERENCES execution_model_dispatches(scope_key,call_identity) ON DELETE RESTRICT""",
}
TABLES = dict(sorted(TABLES.items(), key=lambda item: item[0].startswith("execution_usage_")))
INDEXES = [
    "CREATE INDEX ix_execution_model_dispatches_run ON execution_model_dispatches(scope_key,run_id,created_at)",
]


def upgrade():
    upgrade_connection(op.get_bind())


def upgrade_connection(bind):
    from app.infrastructure.security.tenant_rls import policy_statements

    spec = importlib.util.spec_from_file_location(
        "f07_shape_validator", Path(__file__).with_name("0002artifact_provenance.py")
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    existing = set(sa.inspect(bind).get_table_names(schema="public")) & TABLES.keys()
    if existing:
        helper._validate_existing(bind, TABLES, INDEXES, existing)
    api, kernel = bind.execute(
        sa.text(
            "SELECT current_setting('app.runtime_database_role'),current_setting('app.execution_runtime_role')"
        )
    ).one()
    if not api or not kernel or api == kernel:
        raise RuntimeError("distinct runtime roles required")
    quote = bind.dialect.identifier_preparer.quote
    bind.execute(
        sa.text("""CREATE OR REPLACE FUNCTION public.opencitadel_f07_immutable() RETURNS trigger
    LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'immutable execution accounting'; END $$""")
    )
    for name, ddl in sorted(
        TABLES.items(), key=lambda item: item[0].startswith("execution_usage_")
    ):
        if name not in existing:
            bind.execute(sa.text(f"CREATE TABLE {name} ({ddl})"))
        for statement in policy_statements(name):
            bind.execute(sa.text(statement))
        bind.execute(sa.text(f"REVOKE ALL ON {name} FROM PUBLIC,{quote(api)},{quote(kernel)}"))
        bind.execute(sa.text(f"GRANT SELECT,INSERT ON {name} TO {quote(kernel)}"))
        # Only the admission service may insert configuration under scoped API auth.
        if name == "execution_configurations":
            bind.execute(sa.text(f"GRANT SELECT,INSERT ON {name} TO {quote(api)}"))
        if name == "execution_usage_delivery":
            bind.execute(sa.text(f"GRANT UPDATE ON {name} TO {quote(kernel)}"))
            continue
        bind.execute(sa.text(f"DROP TRIGGER IF EXISTS f07_immutable ON {name}"))
        bind.execute(
            sa.text(
                f"CREATE TRIGGER f07_immutable BEFORE UPDATE OR DELETE ON {name} FOR EACH ROW EXECUTE FUNCTION public.opencitadel_f07_immutable()"
            )
        )
    for sql in INDEXES:
        bind.execute(sa.text(sql.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ")))


def downgrade():
    raise RuntimeError("immutable execution accounting cannot be downgraded")
