"""Scoped immutable evaluation cases, publication membership and upload recovery."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0005evaluation_datasets"
down_revision = "0004execution_configuration"
branch_labels = None
depends_on = None

_SCOPE = """owner_user_id varchar(255), team_id varchar(255),
 scope_key varchar(261) GENERATED ALWAYS AS (CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END) STORED,
 created_by varchar(255) NOT NULL, created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP, schema_version integer NOT NULL DEFAULT 1 CHECK(schema_version=1),
 CHECK ((owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL))"""
TABLES = {
    "evaluation_datasets": f"id uuid NOT NULL, name varchar(255) NOT NULL CHECK(length(trim(name))>0), revision integer NOT NULL CHECK(revision>0), {_SCOPE}, PRIMARY KEY(scope_key,id)",
    "evaluation_object_intents": f"""id uuid NOT NULL, dataset_id uuid NOT NULL, storage_key varchar(255) NOT NULL UNIQUE,
 digest varchar(64) NOT NULL CHECK(digest ~ '^[0-9a-f]{{64}}$'), cleaned_at timestamptz, {_SCOPE}, PRIMARY KEY(scope_key,id),
 UNIQUE(scope_key,dataset_id,id), FOREIGN KEY(scope_key,dataset_id) REFERENCES evaluation_datasets(scope_key,id) ON DELETE RESTRICT""",
    "evaluation_imports": f"""id uuid NOT NULL, dataset_id uuid NOT NULL, object_id uuid, input_digest varchar(64) NOT NULL,
 draft_revision integer NOT NULL CHECK(draft_revision>0), content_type varchar(64) NOT NULL,
 errors jsonb NOT NULL, diff jsonb NOT NULL, expires_at timestamptz NOT NULL, applied boolean NOT NULL DEFAULT false,
 {_SCOPE}, PRIMARY KEY(scope_key,id), FOREIGN KEY(scope_key,dataset_id) REFERENCES evaluation_datasets(scope_key,id) ON DELETE RESTRICT,
 FOREIGN KEY(scope_key,dataset_id,object_id) REFERENCES evaluation_object_intents(scope_key,dataset_id,id) ON DELETE RESTRICT""",
    "evaluation_case_revisions": f"""id uuid NOT NULL, dataset_id uuid NOT NULL, case_key varchar(255) NOT NULL CHECK(length(trim(case_key))>0),
 revision integer NOT NULL CHECK(revision>0), object_id uuid NOT NULL, object_index integer NOT NULL CHECK(object_index BETWEEN 0 AND 999),
 {_SCOPE}, PRIMARY KEY(scope_key,id), UNIQUE(scope_key,dataset_id,id), UNIQUE(scope_key,dataset_id,case_key,revision),
 FOREIGN KEY(scope_key,dataset_id) REFERENCES evaluation_datasets(scope_key,id) ON DELETE RESTRICT,
 FOREIGN KEY(scope_key,dataset_id,object_id) REFERENCES evaluation_object_intents(scope_key,dataset_id,id) ON DELETE RESTRICT""",
    "evaluation_draft_cases": f"""dataset_id uuid NOT NULL, case_key varchar(255) NOT NULL, case_revision_id uuid NOT NULL,
 {_SCOPE}, PRIMARY KEY(scope_key,dataset_id,case_key),
 FOREIGN KEY(scope_key,dataset_id,case_revision_id) REFERENCES evaluation_case_revisions(scope_key,dataset_id,id) ON DELETE RESTRICT""",
    "evaluation_dataset_versions": f"""id uuid NOT NULL, dataset_id uuid NOT NULL, revision integer NOT NULL CHECK(revision>0),
 {_SCOPE}, PRIMARY KEY(scope_key,id), UNIQUE(scope_key,dataset_id,id), UNIQUE(scope_key,dataset_id,revision),
 FOREIGN KEY(scope_key,dataset_id) REFERENCES evaluation_datasets(scope_key,id) ON DELETE RESTRICT""",
    "evaluation_version_cases": f"""version_id uuid NOT NULL, dataset_id uuid NOT NULL, case_key varchar(255) NOT NULL, case_revision_id uuid NOT NULL,
 {_SCOPE}, PRIMARY KEY(scope_key,version_id,case_key),
 FOREIGN KEY(scope_key,dataset_id,version_id) REFERENCES evaluation_dataset_versions(scope_key,dataset_id,id) ON DELETE RESTRICT,
 FOREIGN KEY(scope_key,dataset_id,case_revision_id) REFERENCES evaluation_case_revisions(scope_key,dataset_id,id) ON DELETE RESTRICT""",
    "evaluation_mutations": f"""request_id varchar(255) NOT NULL, fingerprint varchar(64) NOT NULL,
 result jsonb NOT NULL, {_SCOPE}, PRIMARY KEY(scope_key,request_id)""",
}
IMMUTABLE = {
    "evaluation_case_revisions",
    "evaluation_dataset_versions",
    "evaluation_version_cases",
    "evaluation_mutations",
}
INDEXES = [
    "CREATE INDEX ix_evaluation_objects_cleanup ON evaluation_object_intents(cleaned_at,updated_at)"
]


def upgrade():
    upgrade_connection(op.get_bind())


def upgrade_connection(bind):
    from app.infrastructure.security.tenant_rls import policy_statements

    spec = importlib.util.spec_from_file_location(
        "e01_shape", Path(__file__).with_name("0002artifact_provenance.py")
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
            """CREATE OR REPLACE FUNCTION public.opencitadel_e01_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'immutable evaluation fact'; END $$"""
        )
    )
    bind.execute(
        sa.text("""CREATE OR REPLACE FUNCTION public.opencitadel_e01_intent_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
      IF ROW(NEW.id,NEW.dataset_id,NEW.storage_key,NEW.digest,NEW.owner_user_id,NEW.team_id,NEW.created_by,NEW.created_at)
      IS DISTINCT FROM ROW(OLD.id,OLD.dataset_id,OLD.storage_key,OLD.digest,OLD.owner_user_id,OLD.team_id,OLD.created_by,OLD.created_at)
      THEN RAISE EXCEPTION 'immutable evaluation object identity'; END IF; RETURN NEW; END $$""")
    )
    for name, ddl in TABLES.items():
        if name not in existing:
            bind.execute(sa.text(f"CREATE TABLE {name} ({ddl})"))
        for statement in policy_statements(name):
            bind.execute(sa.text(statement))
        bind.execute(sa.text(f"REVOKE ALL ON {name} FROM PUBLIC,{quote(api)},{quote(kernel)}"))
        grants = "SELECT,INSERT" if name in IMMUTABLE else "SELECT,INSERT,UPDATE"
        if name == "evaluation_draft_cases":
            grants += ",DELETE"
        bind.execute(sa.text(f"GRANT {grants} ON {name} TO {quote(api)},{quote(kernel)}"))
        if name in IMMUTABLE:
            bind.execute(sa.text(f"DROP TRIGGER IF EXISTS e01_immutable ON {name}"))
            bind.execute(
                sa.text(
                    f"CREATE TRIGGER e01_immutable BEFORE UPDATE OR DELETE ON {name} FOR EACH ROW EXECUTE FUNCTION public.opencitadel_e01_immutable()"
                )
            )
        if name == "evaluation_object_intents":
            bind.execute(sa.text(f"DROP TRIGGER IF EXISTS e01_intent_guard ON {name}"))
            bind.execute(
                sa.text(
                    f"CREATE TRIGGER e01_intent_guard BEFORE UPDATE ON {name} FOR EACH ROW EXECUTE FUNCTION public.opencitadel_e01_intent_guard()"
                )
            )
    for sql in INDEXES:
        bind.execute(sa.text(sql.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ")))


def downgrade():
    raise RuntimeError("immutable evaluation versions cannot be downgraded")
