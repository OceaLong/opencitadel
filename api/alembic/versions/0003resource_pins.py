"""Scoped durable retention and separately authorized immutable content authority."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0003resource_pins"
down_revision = "0002artifact_provenance"
branch_labels = None
depends_on = None

_SCOPE = """owner_user_id varchar(255), team_id varchar(255),
 scope_key varchar(261) GENERATED ALWAYS AS (CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END) STORED,
 created_by varchar(255) NOT NULL, created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP, schema_version integer NOT NULL DEFAULT 1 CHECK(schema_version>0),
 CHECK ((owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL))"""
TABLES = {
    "artifact_retired_objects": f"""retirement_id uuid PRIMARY KEY, artifact_id varchar(255) NOT NULL, session_id varchar(255) NOT NULL,
 storage_key text NOT NULL, cleaned_at timestamptz, {_SCOPE}, UNIQUE(scope_key,artifact_id,storage_key)""",
    "execution_public_content": f"""content_id uuid PRIMARY KEY, command_id uuid NOT NULL,
 run_id uuid NOT NULL, activity_id uuid NOT NULL, generation integer NOT NULL CHECK(generation>=0), claim_generation integer NOT NULL CHECK(claim_generation>0),
 phase varchar(8) NOT NULL CHECK(phase IN ('input','output')), body text NOT NULL, content_digest varchar(64) NOT NULL,
 byte_length bigint NOT NULL CHECK(byte_length>=0), redacted boolean NOT NULL, citation_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
 {_SCOPE}, UNIQUE(scope_key,command_id,phase)""",
    "execution_content_bindings": f"""event_id uuid NOT NULL, phase varchar(8) NOT NULL CHECK(phase IN ('input','output')),
 content_id uuid NOT NULL REFERENCES execution_public_content(content_id) ON DELETE RESTRICT,
 run_id uuid NOT NULL, step_id varchar(255) NOT NULL, formal_position bigint NOT NULL CHECK(formal_position>0),
 {_SCOPE}, PRIMARY KEY(event_id,phase)""",
    "resource_pins": f"""id uuid PRIMARY KEY, owner_kind varchar(64) NOT NULL CHECK(length(owner_kind)>0), owner_id varchar(255) NOT NULL CHECK(length(owner_id)>0),
 resource_kind varchar(32) NOT NULL CHECK(resource_kind IN ('knowledge_base','artifact','file','execution_content')),
 resource_id varchar(255) NOT NULL CHECK(length(resource_id)>0), resource_version varchar(255) NOT NULL CHECK(length(resource_version)>0),
 available boolean NOT NULL DEFAULT true, unavailable_at timestamptz, unavailable_reason varchar(128), {_SCOPE},
 CHECK ((available AND unavailable_at IS NULL AND unavailable_reason IS NULL) OR (NOT available AND unavailable_at IS NOT NULL AND unavailable_reason IS NOT NULL)),
 UNIQUE(scope_key,owner_kind,owner_id,resource_kind,resource_id,resource_version)""",
}
INDEXES = [
    "CREATE INDEX ix_resource_pins_resource ON resource_pins(resource_kind,resource_id,resource_version) WHERE available",
    "CREATE INDEX ix_resource_pins_owner ON resource_pins(scope_key,owner_kind,owner_id)",
]


def upgrade():
    upgrade_connection(op.get_bind())


def upgrade_connection(bind):
    from app.infrastructure.security.tenant_rls import policy_statements

    spec = importlib.util.spec_from_file_location(
        "f06_shape_validator", Path(__file__).with_name("0002artifact_provenance.py")
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
    for name, ddl in TABLES.items():
        if name not in existing:
            bind.execute(sa.text(f"CREATE TABLE {name} ({ddl})"))
        for statement in policy_statements(name):
            bind.execute(sa.text(statement))
        bind.execute(sa.text(f"REVOKE ALL ON {name} FROM PUBLIC,{quote(api)},{quote(kernel)}"))
        bind.execute(
            sa.text(f"GRANT SELECT,INSERT,UPDATE,DELETE ON {name} TO {quote(api)},{quote(kernel)}")
        )
    bind.execute(sa.text(f"REVOKE ALL ON artifact_retired_objects FROM {quote(api)}"))
    bind.execute(sa.text(f"REVOKE DELETE ON artifact_retired_objects FROM {quote(kernel)}"))
    bind.execute(
        sa.text("""CREATE OR REPLACE FUNCTION public.opencitadel_capture_retired_artifact() RETURNS trigger
      LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
      BEGIN
        IF TG_TABLE_NAME='sessions' THEN
          INSERT INTO public.artifact_retired_objects(retirement_id,artifact_id,session_id,storage_key,owner_user_id,team_id,created_by)
          SELECT gen_random_uuid(),a.id,a.session_id,k,CASE WHEN OLD.team_id IS NULL THEN OLD.owner_user_id ELSE NULL END,OLD.team_id,'artifact-purge'
          FROM public.artifacts a CROSS JOIN LATERAL jsonb_array_elements_text(a.version_refs) k
          WHERE a.session_id=OLD.id AND length(k)>0 AND (OLD.owner_user_id IS NOT NULL OR OLD.team_id IS NOT NULL)
          ON CONFLICT(scope_key,artifact_id,storage_key) DO NOTHING;
        ELSIF TG_TABLE_NAME='artifacts' THEN
          INSERT INTO public.artifact_retired_objects(retirement_id,artifact_id,session_id,storage_key,owner_user_id,team_id,created_by)
          SELECT gen_random_uuid(),OLD.id,OLD.session_id,k,CASE WHEN s.team_id IS NULL THEN s.owner_user_id ELSE NULL END,s.team_id,'artifact-purge'
          FROM public.sessions s CROSS JOIN LATERAL jsonb_array_elements_text(OLD.version_refs) k
          WHERE s.id=OLD.session_id AND length(k)>0 AND (s.owner_user_id IS NOT NULL OR s.team_id IS NOT NULL)
          ON CONFLICT(scope_key,artifact_id,storage_key) DO NOTHING;
        END IF;
        RETURN OLD;
      END $$""")
    )
    bind.execute(
        sa.text(
            f"REVOKE ALL ON FUNCTION public.opencitadel_capture_retired_artifact() FROM PUBLIC,{quote(api)}"
        )
    )
    for table in ("sessions", "artifacts"):
        bind.execute(sa.text(f"DROP TRIGGER IF EXISTS artifact_retirement_capture ON {table}"))
        bind.execute(
            sa.text(
                f"CREATE TRIGGER artifact_retirement_capture BEFORE DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION public.opencitadel_capture_retired_artifact()"
            )
        )
    for name in ("execution_public_content", "execution_content_bindings"):
        bind.execute(sa.text(f"REVOKE INSERT,UPDATE,DELETE ON {name} FROM {quote(api)}"))
        bind.execute(sa.text(f"REVOKE UPDATE,DELETE ON {name} FROM {quote(kernel)}"))
    bind.execute(
        sa.text("""CREATE OR REPLACE FUNCTION public.opencitadel_content_immutable() RETURNS trigger
       LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'execution content is immutable'; END $$""")
    )
    for name in ("execution_public_content", "execution_content_bindings"):
        bind.execute(sa.text(f"DROP TRIGGER IF EXISTS execution_content_immutable ON {name}"))
        bind.execute(
            sa.text(
                f"CREATE TRIGGER execution_content_immutable BEFORE UPDATE OR DELETE ON {name} FOR EACH ROW EXECUTE FUNCTION public.opencitadel_content_immutable()"
            )
        )
    for statement in INDEXES:
        bind.execute(sa.text(statement.replace("CREATE INDEX", "CREATE INDEX IF NOT EXISTS")))
    file_columns = {c["name"]: c for c in sa.inspect(bind).get_columns("files")}
    additions = {
        "content_digest": "varchar(64)",
        "object_identity": "uuid",
        "content_available": "boolean NOT NULL DEFAULT true",
    }
    present = set(additions) & file_columns.keys()
    if present and present != set(additions):
        raise RuntimeError("resource pins schema mismatch: partial file identity columns")
    if not present:
        for name, ddl in additions.items():
            bind.execute(sa.text(f"ALTER TABLE files ADD COLUMN {name} {ddl}"))
        bind.execute(
            sa.text(
                "ALTER TABLE files ADD CONSTRAINT ck_files_content_identity CHECK ((content_digest IS NULL AND object_identity IS NULL) OR (content_digest IS NOT NULL AND content_digest ~ '^[0-9a-f]{64}$' AND object_identity IS NOT NULL))"
            )
        )
    else:
        if (
            str(file_columns["content_digest"]["type"]) != "VARCHAR(64)"
            or not file_columns["content_digest"]["nullable"]
            or str(file_columns["object_identity"]["type"]) != "UUID"
            or not file_columns["object_identity"]["nullable"]
            or str(file_columns["content_available"]["type"]) != "BOOLEAN"
            or file_columns["content_available"]["nullable"]
            or file_columns["content_available"]["default"] != "true"
        ):
            raise RuntimeError("resource pins schema mismatch: file identity shape")
        checks = {c["name"]: c["sqltext"] for c in sa.inspect(bind).get_check_constraints("files")}
        actual = checks.get("ck_files_content_identity")
        previous = "content_digest IS NULL AND object_identity IS NULL OR content_digest::text ~ '^[0-9a-f]{64}$'::text AND object_identity IS NOT NULL"
        expected = "content_digest IS NULL AND object_identity IS NULL OR content_digest IS NOT NULL AND content_digest::text ~ '^[0-9a-f]{64}$'::text AND object_identity IS NOT NULL"
        if actual == previous:
            bind.execute(sa.text("ALTER TABLE files DROP CONSTRAINT ck_files_content_identity"))
            bind.execute(
                sa.text(
                    "ALTER TABLE files ADD CONSTRAINT ck_files_content_identity CHECK ((content_digest IS NULL AND object_identity IS NULL) OR (content_digest IS NOT NULL AND content_digest ~ '^[0-9a-f]{64}$' AND object_identity IS NOT NULL))"
                )
            )
        elif actual != expected:
            raise RuntimeError("resource pins schema mismatch: file identity constraint")
    bind.execute(
        sa.text("""CREATE OR REPLACE FUNCTION public.opencitadel_file_identity_guard() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF OLD.content_digest IS NOT NULL AND (NEW.key IS DISTINCT FROM OLD.key OR NEW.content_digest IS DISTINCT FROM OLD.content_digest OR NEW.object_identity IS DISTINCT FROM OLD.object_identity) THEN
          RAISE EXCEPTION 'immutable file identity cannot change';
        END IF;
        IF NOT OLD.content_available AND NEW.content_available THEN RAISE EXCEPTION 'deleted file authority cannot revive'; END IF;
        RETURN NEW;
      END $$""")
    )
    bind.execute(sa.text("DROP TRIGGER IF EXISTS file_content_identity_guard ON files"))
    bind.execute(
        sa.text(
            "CREATE TRIGGER file_content_identity_guard BEFORE UPDATE ON files FOR EACH ROW EXECUTE FUNCTION public.opencitadel_file_identity_guard()"
        )
    )
    # Covers direct repository and FK cascades, including broad owner deletion.
    # SECURITY INVOKER preserves tenant RLS; all pins have the resource's scope.
    bind.execute(
        sa.text("""CREATE OR REPLACE FUNCTION public.opencitadel_pin_delete_guard() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
    DECLARE blocked boolean;
    BEGIN
      IF TG_TABLE_NAME='sessions' THEN
        SELECT EXISTS(SELECT 1 FROM resource_pins p JOIN artifacts a ON a.id=p.resource_id
          WHERE p.available AND p.resource_kind='artifact' AND a.session_id=OLD.id) INTO blocked;
      ELSIF TG_TABLE_NAME='users' THEN
        SELECT EXISTS(SELECT 1 FROM resource_pins WHERE available AND owner_user_id=OLD.id) INTO blocked;
      ELSIF TG_TABLE_NAME='teams' THEN
        SELECT EXISTS(SELECT 1 FROM resource_pins WHERE available AND team_id=OLD.id) INTO blocked;
      ELSIF TG_TABLE_NAME='knowledge_base_versions' THEN
        SELECT EXISTS(SELECT 1 FROM resource_pins WHERE available AND resource_kind='knowledge_base'
          AND resource_id=OLD.knowledge_base_id AND resource_version=OLD.id) INTO blocked;
      ELSE
        SELECT EXISTS(SELECT 1 FROM resource_pins WHERE available AND resource_id=OLD.id
          AND resource_kind=CASE TG_TABLE_NAME WHEN 'knowledge_bases' THEN 'knowledge_base' WHEN 'artifacts' THEN 'artifact' WHEN 'files' THEN 'file' END) INTO blocked;
      END IF;
      IF blocked THEN RAISE EXCEPTION 'resource is pinned' USING ERRCODE='23503'; END IF;
      RETURN OLD;
    END $$""")
    )
    for table in (
        "knowledge_bases",
        "knowledge_base_versions",
        "files",
        "artifacts",
        "sessions",
        "users",
        "teams",
    ):
        bind.execute(sa.text(f"DROP TRIGGER IF EXISTS resource_pin_delete_guard ON {table}"))
        bind.execute(
            sa.text(
                f"CREATE TRIGGER resource_pin_delete_guard BEFORE DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION public.opencitadel_pin_delete_guard()"
            )
        )


def downgrade():
    raise RuntimeError("resource pins require explicit retained evidence backup restore")
