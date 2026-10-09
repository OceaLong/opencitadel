"""Controlled recording jobs, immutable metadata and idempotent replay claims."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0007evaluation_recordings"
down_revision = "0006evaluation_configuration"
branch_labels = None
depends_on = None

_SCOPE = """owner_user_id varchar(255), team_id varchar(255),
 scope_key varchar(261) GENERATED ALWAYS AS (CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END) STORED,
 created_by varchar(255) NOT NULL, created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK ((owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL))"""
TABLES = {
    "evaluation_recording_jobs": f"""id uuid NOT NULL, source_run_id uuid NOT NULL, revision integer NOT NULL DEFAULT 1 CHECK(revision>0), status varchar(16) NOT NULL CHECK(status IN ('queued','running','ready','failed')), selection jsonb NOT NULL, principal jsonb NOT NULL, claim_token uuid, lease_until timestamptz, result_version uuid, error varchar(128), {_SCOPE}, PRIMARY KEY(scope_key,id)""",
    "evaluation_recording_objects": f"""id uuid NOT NULL, job_id uuid NOT NULL, storage_key text NOT NULL UNIQUE, digest varchar(64) NOT NULL CHECK(digest ~ '^[0-9a-f]{{64}}$'), size_bytes integer NOT NULL CHECK(size_bytes BETWEEN 1 AND 20971520), cleaned_at timestamptz, updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP, {_SCOPE}, PRIMARY KEY(scope_key,id), FOREIGN KEY(scope_key,job_id) REFERENCES evaluation_recording_jobs(scope_key,id) ON DELETE RESTRICT""",
    "evaluation_recording_versions": f"""id uuid NOT NULL, job_id uuid NOT NULL, body jsonb NOT NULL, digest varchar(64) NOT NULL, {_SCOPE}, PRIMARY KEY(scope_key,id), UNIQUE(scope_key,job_id), FOREIGN KEY(scope_key,job_id) REFERENCES evaluation_recording_jobs(scope_key,id) ON DELETE RESTRICT""",
    "evaluation_recording_slots": f"""id uuid NOT NULL, version_id uuid NOT NULL, match_key varchar(64) NOT NULL, object_id uuid NOT NULL, body jsonb NOT NULL, {_SCOPE}, PRIMARY KEY(scope_key,id), UNIQUE(scope_key,version_id,match_key), UNIQUE(scope_key,version_id,id), FOREIGN KEY(scope_key,version_id) REFERENCES evaluation_recording_versions(scope_key,id) ON DELETE RESTRICT, FOREIGN KEY(scope_key,object_id) REFERENCES evaluation_recording_objects(scope_key,id) ON DELETE RESTRICT""",
    "evaluation_contract_captures": f"""run_id uuid NOT NULL, activity_id uuid NOT NULL, body jsonb NOT NULL, digest varchar(64) NOT NULL, {_SCOPE}, PRIMARY KEY(scope_key,activity_id,digest)""",
    "evaluation_replay_bindings": f"""run_id uuid NOT NULL, version_id uuid NOT NULL, principal jsonb NOT NULL, admission jsonb NOT NULL DEFAULT '{{}}'::jsonb, {_SCOPE}, PRIMARY KEY(scope_key,run_id), FOREIGN KEY(scope_key,version_id) REFERENCES evaluation_recording_versions(scope_key,id) ON DELETE RESTRICT""",
    "evaluation_replay_ledger": f"""run_id uuid NOT NULL, activity_id uuid NOT NULL, version_id uuid NOT NULL, slot_id uuid NOT NULL, match_key varchar(64) NOT NULL, {_SCOPE}, PRIMARY KEY(scope_key,run_id,activity_id), UNIQUE(scope_key,run_id,version_id,slot_id), FOREIGN KEY(scope_key,run_id) REFERENCES evaluation_replay_bindings(scope_key,run_id) ON DELETE RESTRICT, FOREIGN KEY(scope_key,version_id,slot_id) REFERENCES evaluation_recording_slots(scope_key,version_id,id) ON DELETE RESTRICT""",
}
INDEXES = [
    "CREATE INDEX ix_recording_jobs_claim ON evaluation_recording_jobs(status,lease_until,created_at)"
]
MUTABLE = {"evaluation_recording_jobs", "evaluation_recording_objects"}
KERNEL_ONLY = {
    "evaluation_contract_captures",
    "evaluation_replay_bindings",
    "evaluation_replay_ledger",
}


def upgrade():
    upgrade_connection(op.get_bind())


def upgrade_connection(bind):
    from app.infrastructure.security.tenant_rls import policy_statements

    spec = importlib.util.spec_from_file_location(
        "e03_shape", Path(__file__).with_name("0002artifact_provenance.py")
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
        sa.text(
            "CREATE OR REPLACE FUNCTION public.opencitadel_e03_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'immutable recording fact'; END $$"
        )
    )
    for name, ddl in TABLES.items():
        if name not in existing:
            bind.execute(sa.text(f"CREATE TABLE {name} ({ddl})"))
        for statement in policy_statements(name):
            bind.execute(sa.text(statement))
        bind.execute(sa.text(f"REVOKE ALL ON {name} FROM PUBLIC,{quote(api)},{quote(kernel)}"))
        grants = "SELECT,INSERT,UPDATE" if name in MUTABLE else "SELECT,INSERT"
        bind.execute(sa.text(f"GRANT {grants} ON {name} TO {quote(kernel)}"))
        bind.execute(
            sa.text(
                f"GRANT {'SELECT' if name in KERNEL_ONLY else grants} ON {name} TO {quote(api)}"
            )
        )
        if name not in MUTABLE:
            bind.execute(sa.text(f"DROP TRIGGER IF EXISTS e03_immutable ON {name}"))
            bind.execute(
                sa.text(
                    f"CREATE TRIGGER e03_immutable BEFORE UPDATE OR DELETE ON {name} FOR EACH ROW EXECUTE FUNCTION public.opencitadel_e03_immutable()"
                )
            )
    bind.execute(
        sa.text("""CREATE OR REPLACE FUNCTION public.opencitadel_e03_object_identity() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF (NEW.id,NEW.job_id,NEW.storage_key,NEW.digest,NEW.size_bytes,NEW.owner_user_id,NEW.team_id,NEW.created_by) IS DISTINCT FROM (OLD.id,OLD.job_id,OLD.storage_key,OLD.digest,OLD.size_bytes,OLD.owner_user_id,OLD.team_id,OLD.created_by) OR (OLD.cleaned_at IS NOT NULL AND NEW.cleaned_at IS DISTINCT FROM OLD.cleaned_at) THEN RAISE EXCEPTION 'immutable recording object identity'; END IF; RETURN NEW; END $$""")
    )
    bind.execute(
        sa.text("DROP TRIGGER IF EXISTS e03_object_identity ON evaluation_recording_objects")
    )
    bind.execute(
        sa.text(
            "CREATE TRIGGER e03_object_identity BEFORE UPDATE ON evaluation_recording_objects FOR EACH ROW EXECUTE FUNCTION public.opencitadel_e03_object_identity()"
        )
    )
    for statement in INDEXES:
        bind.execute(sa.text(statement.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ")))


def downgrade():
    raise RuntimeError("recording facts cannot be downgraded")
