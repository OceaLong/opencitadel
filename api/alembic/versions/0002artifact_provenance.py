"""Durable private artifact production receipts and upload cleanup intents."""

import sqlalchemy as sa

from alembic import op

revision = "0002artifact_provenance"
down_revision = "0002view_queries"
branch_labels = None
depends_on = None

_SCOPE = """owner_user_id varchar(255), team_id varchar(255),
 scope_key varchar(261) GENERATED ALWAYS AS (CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END) STORED,
 created_by varchar(255) NOT NULL, created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP, schema_version integer NOT NULL DEFAULT 1 CHECK(schema_version>0),
 CHECK ((owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL))"""
RECONCILIATION_COLUMNS = {
    "reconciliation_status": "varchar(20) NOT NULL DEFAULT 'pending' CHECK(reconciliation_status IN ('pending','published','bound','unavailable'))",
    "retry_attempts": "integer NOT NULL DEFAULT 0 CHECK(retry_attempts>=0)",
    "next_attempt_at": "timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP",
    "last_error": "varchar(128)",
    "settled_at": "timestamptz",
}
_RECONCILIATION = "".join(
    ", " + name + " " + definition for name, definition in RECONCILIATION_COLUMNS.items()
)
TABLES = {
    "artifact_upload_intents": f"""upload_id uuid PRIMARY KEY, session_id varchar(255) NOT NULL,
 artifact_id varchar(255) NOT NULL, storage_key text NOT NULL UNIQUE,
 cleaned_at timestamptz, {_SCOPE}""",
    "artifact_production_receipts": f"""operation_id uuid PRIMARY KEY,
 association_id uuid NOT NULL UNIQUE REFERENCES artifact_version_provenance(id) ON DELETE RESTRICT,
 artifact_id varchar(255) NOT NULL, version integer NOT NULL CHECK(version>0),
 run_id uuid NOT NULL, activity_id uuid NOT NULL, generation integer NOT NULL CHECK(generation>=0),
 claim_generation integer NOT NULL CHECK(claim_generation>0), storage_key text NOT NULL,
 content_digest varchar(255) NOT NULL, event_id uuid, event_position bigint CHECK(event_position>0),
 bound_at timestamptz, {_SCOPE}, invocation_id uuid{_RECONCILIATION}, CHECK ((event_id IS NULL) = (event_position IS NULL))""",
}
INDEXES = [
    "CREATE INDEX ix_artifact_upload_cleanup ON artifact_upload_intents(created_at) WHERE cleaned_at IS NULL",
    "CREATE INDEX ix_artifact_receipt_pending ON artifact_production_receipts(created_at) WHERE bound_at IS NULL",
]


def upgrade():
    upgrade_connection(op.get_bind())


def upgrade_connection(bind):
    from app.infrastructure.security.tenant_rls import policy_statements

    existing = set(sa.inspect(bind).get_table_names(schema="public")) & TABLES.keys()
    if "artifact_production_receipts" in existing:
        columns = {
            column["name"]
            for column in sa.inspect(bind).get_columns("artifact_production_receipts")
        }
        old_tables = dict(TABLES)
        if "reconciliation_status" not in columns:
            old_tables = {
                name: ddl.replace(_RECONCILIATION, "") for name, ddl in old_tables.items()
            }
        if "invocation_id" not in columns:
            old_tables = {
                name: ddl.replace(", invocation_id uuid", "") for name, ddl in old_tables.items()
            }
        _validate_existing(bind, old_tables, INDEXES, existing)
        if "invocation_id" not in columns:
            bind.execute(
                sa.text("ALTER TABLE artifact_production_receipts ADD COLUMN invocation_id uuid")
            )
        if "reconciliation_status" not in columns:
            for name, definition in RECONCILIATION_COLUMNS.items():
                bind.execute(
                    sa.text(
                        f"ALTER TABLE artifact_production_receipts ADD COLUMN {name} {definition}"
                    )
                )
    if existing:
        _validate_existing(bind, TABLES, INDEXES, existing)
    api, kernel = bind.execute(
        sa.text(
            "SELECT current_setting('app.runtime_database_role'),current_setting('app.execution_runtime_role')"
        )
    ).one()
    if not api or not kernel or api == kernel:
        raise RuntimeError("distinct runtime roles required")
    quote = bind.dialect.identifier_preparer.quote
    for name, columns in TABLES.items():
        if name not in existing:
            bind.execute(sa.text(f"CREATE TABLE {name} ({columns})"))
        for statement in policy_statements(name):
            bind.execute(sa.text(statement))
        bind.execute(sa.text(f"REVOKE ALL ON {name} FROM PUBLIC,{quote(api)},{quote(kernel)}"))
        bind.execute(sa.text(f"GRANT INSERT ON {name} TO {quote(api)}"))
        bind.execute(sa.text(f"GRANT SELECT,INSERT,UPDATE ON {name} TO {quote(kernel)}"))
    for statement in INDEXES:
        bind.execute(sa.text(statement.replace("CREATE INDEX", "CREATE INDEX IF NOT EXISTS")))
    bind.execute(
        sa.text("""
    CREATE OR REPLACE FUNCTION public.opencitadel_artifact_receipt_guard()
    RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
    BEGIN
      IF NOT public.opencitadel_authorization_valid() THEN
        RAISE EXCEPTION 'invalid artifact authorization' USING ERRCODE='42501';
      END IF;
      IF TG_OP='UPDATE' THEN
        IF TG_TABLE_NAME='artifact_upload_intents' THEN
          IF (to_jsonb(NEW)-'scope_key'-'cleaned_at'-'updated_at') IS DISTINCT FROM (to_jsonb(OLD)-'scope_key'-'cleaned_at'-'updated_at')
             OR (OLD.cleaned_at IS NOT NULL AND NEW.cleaned_at IS DISTINCT FROM OLD.cleaned_at) THEN
            RAISE EXCEPTION 'immutable upload identity' USING ERRCODE='23514';
          END IF;
        ELSE
          IF (to_jsonb(NEW)-'scope_key'-'event_id'-'event_position'-'bound_at'-'updated_at'-'reconciliation_status'-'retry_attempts'-'next_attempt_at'-'last_error'-'settled_at') IS DISTINCT FROM
             (to_jsonb(OLD)-'scope_key'-'event_id'-'event_position'-'bound_at'-'updated_at'-'reconciliation_status'-'retry_attempts'-'next_attempt_at'-'last_error'-'settled_at')
             OR (OLD.event_id IS NOT NULL AND (NEW.event_id IS DISTINCT FROM OLD.event_id OR NEW.event_position IS DISTINCT FROM OLD.event_position))
             OR (OLD.bound_at IS NOT NULL AND NEW.bound_at IS DISTINCT FROM OLD.bound_at) THEN
            RAISE EXCEPTION 'immutable production identity' USING ERRCODE='23514';
          END IF;
        END IF;
        RETURN NEW;
      END IF;
      IF TG_TABLE_NAME='artifact_upload_intents' THEN
        IF NEW.cleaned_at IS NOT NULL OR NEW.storage_key <> 'artifacts/' || NEW.session_id || '/' || NEW.artifact_id || '/uploads/' || NEW.upload_id::text
          OR NOT EXISTS(SELECT 1 FROM public.sessions s WHERE s.id=NEW.session_id AND
             ((NEW.team_id IS NOT NULL AND s.team_id=NEW.team_id) OR (NEW.team_id IS NULL AND s.team_id IS NULL AND s.owner_user_id=NEW.owner_user_id))) THEN
          RAISE EXCEPTION 'upload scope mismatch' USING ERRCODE='23514';
        END IF;
      ELSE
        IF NEW.event_id IS NOT NULL OR NEW.bound_at IS NOT NULL OR NEW.reconciliation_status<>'pending' OR NEW.retry_attempts<>0 OR NEW.last_error IS NOT NULL OR NEW.settled_at IS NOT NULL OR NOT EXISTS (
          SELECT 1 FROM public.artifact_version_provenance p JOIN public.artifacts a ON a.id=p.artifact_id
          WHERE p.id=NEW.association_id AND p.artifact_id=NEW.artifact_id AND p.version=NEW.version
            AND p.producer_identity=NEW.operation_id::text AND p.binding_status='pending'
            AND p.content_digest=NEW.content_digest AND a.version_refs->>(NEW.version-1)=NEW.storage_key
            AND p.owner_user_id IS NOT DISTINCT FROM NEW.owner_user_id AND p.team_id IS NOT DISTINCT FROM NEW.team_id
        ) OR NOT EXISTS (
          SELECT 1 FROM public.execution_activity_tasks a WHERE a.activity_id=NEW.activity_id
            AND a.run_id=NEW.run_id::text AND a.request_generation=NEW.generation AND a.claim_generation=NEW.claim_generation
            AND a.owner_user_id IS NOT DISTINCT FROM NEW.owner_user_id AND a.team_id IS NOT DISTINCT FROM NEW.team_id
            AND (NEW.invocation_id IS NULL OR EXISTS (SELECT 1 FROM public.execution_events e
              WHERE e.position=a.request_event_position AND e.public_payload->>'invocation_id'=NEW.invocation_id::text))
        ) THEN RAISE EXCEPTION 'production receipt mismatch' USING ERRCODE='23514'; END IF;
      END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION public.opencitadel_artifact_receipt_guard() FROM PUBLIC;
    """)
    )
    for name in TABLES:
        bind.execute(sa.text(f"DROP TRIGGER IF EXISTS artifact_receipt_guard ON {name}"))
        bind.execute(
            sa.text(
                f"CREATE TRIGGER artifact_receipt_guard BEFORE INSERT OR UPDATE ON {name} FOR EACH ROW EXECUTE FUNCTION public.opencitadel_artifact_receipt_guard()"
            )
        )

    bind.execute(
        sa.text(
            """
    CREATE OR REPLACE FUNCTION public.opencitadel_artifact_version_guard()
    RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$
    BEGIN
      IF NOT public.opencitadel_authorization_valid() THEN
        RAISE EXCEPTION 'invalid artifact authorization' USING ERRCODE='42501';
      END IF;
      IF TG_OP='INSERT' THEN
        IF NEW.binding_status='bound' OR NEW.producer_run_id IS NOT NULL OR NEW.activity_id IS NOT NULL
          OR NEW.attempt_id IS NOT NULL OR NEW.invocation_id IS NOT NULL OR NEW.produced_event_id IS NOT NULL
          OR NEW.boundary IS NOT NULL OR coalesce(NEW.producer_step_ids,'[]'::jsonb) NOT IN ('[]'::jsonb,'null'::jsonb)
          OR NOT EXISTS (SELECT 1 FROM public.artifacts a WHERE a.id=NEW.artifact_id
             AND jsonb_typeof(a.version_refs)='array' AND NEW.version BETWEEN 1 AND jsonb_array_length(a.version_refs)
             AND nullif(a.version_refs->>(NEW.version-1),'') IS NOT NULL) THEN
          RAISE EXCEPTION 'unverified artifact insertion' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END IF;
      IF NEW.binding_status='bound' AND OLD.binding_status<>'bound'
        AND NOT pg_has_role(COALESCE(NULLIF(current_setting('role',true),'none'),session_user)::name,
          '__KERNEL_ROLE__','MEMBER') THEN
        RAISE EXCEPTION 'kernel artifact binding required' USING ERRCODE='42501';
      END IF;
      IF NEW.binding_status<>'bound' AND
        (NEW.producer_run_id,NEW.activity_id,NEW.attempt_id,NEW.invocation_id,NEW.producer_step_ids,NEW.produced_event_id,NEW.boundary)
        IS DISTINCT FROM (OLD.producer_run_id,OLD.activity_id,OLD.attempt_id,OLD.invocation_id,OLD.producer_step_ids,OLD.produced_event_id,OLD.boundary) THEN
        RAISE EXCEPTION 'pending artifact authority must remain private' USING ERRCODE='23514';
      END IF;
      IF (NEW.id,NEW.artifact_id,NEW.version,NEW.producer_identity,NEW.content_digest,NEW.content_ref,NEW.evidence_kind,NEW.evidence,NEW.citation_refs,NEW.owner_user_id,NEW.team_id,NEW.created_by,NEW.created_at,NEW.schema_version)
         IS DISTINCT FROM
         (OLD.id,OLD.artifact_id,OLD.version,OLD.producer_identity,OLD.content_digest,OLD.content_ref,OLD.evidence_kind,OLD.evidence,OLD.citation_refs,OLD.owner_user_id,OLD.team_id,OLD.created_by,OLD.created_at,OLD.schema_version)
         OR (OLD.binding_status='bound' AND
           (NEW.producer_run_id,NEW.activity_id,NEW.attempt_id,NEW.invocation_id,NEW.producer_step_ids,NEW.produced_event_id,NEW.boundary,NEW.binding_status)
            IS DISTINCT FROM
           (OLD.producer_run_id,OLD.activity_id,OLD.attempt_id,OLD.invocation_id,OLD.producer_step_ids,OLD.produced_event_id,OLD.boundary,OLD.binding_status)) THEN
        RAISE EXCEPTION 'immutable artifact version' USING ERRCODE='23514';
      END IF;
      IF NEW.binding_status='bound' AND OLD.binding_status<>'bound' AND NOT EXISTS (
        SELECT 1 FROM public.artifact_production_receipts r
        WHERE r.association_id=NEW.id AND r.run_id=NEW.producer_run_id AND r.activity_id=NEW.activity_id
          AND r.event_id=NEW.produced_event_id AND r.event_position=NEW.boundary
          AND r.owner_user_id IS NOT DISTINCT FROM NEW.owner_user_id AND r.team_id IS NOT DISTINCT FROM NEW.team_id
      ) THEN RAISE EXCEPTION 'unverified artifact binding' USING ERRCODE='23514'; END IF;
      RETURN NEW;
    END $$;
    REVOKE ALL ON FUNCTION public.opencitadel_artifact_version_guard() FROM PUBLIC;
    DROP TRIGGER IF EXISTS artifact_version_immutable ON artifact_version_provenance;
    DROP TRIGGER IF EXISTS z_artifact_version_immutable ON artifact_version_provenance;
    CREATE TRIGGER z_artifact_version_immutable BEFORE INSERT OR UPDATE ON artifact_version_provenance
      FOR EACH ROW EXECUTE FUNCTION public.opencitadel_artifact_version_guard();
    """.replace("__KERNEL_ROLE__", kernel.replace("'", "''"))
        )
    )

    bind.execute(
        sa.text("""UPDATE artifact_production_receipts
      SET reconciliation_status=CASE WHEN bound_at IS NOT NULL THEN 'bound' ELSE 'published' END
      WHERE event_id IS NOT NULL AND reconciliation_status='pending'""")
    )


def downgrade():
    raise RuntimeError("artifact provenance requires explicit retained evidence backup restore")


def _validate_existing(bind, tables, indexes, existing):
    """Compare PostgreSQL-normalized fixed DDL before touching precreated tables."""
    import json
    from uuid import uuid4

    scratch = "artifact_provenance_shape_" + uuid4().hex
    old_path = bind.scalar(sa.text("SHOW search_path"))
    bind.execute(sa.text(f'CREATE SCHEMA "{scratch}"'))
    try:
        bind.execute(sa.text(f'SET LOCAL search_path TO "{scratch}", public'))
        for name, columns in tables.items():
            bind.execute(sa.text(f"CREATE TABLE {name} ({columns})"))
        for statement in indexes:
            bind.execute(sa.text(statement))
        inspector = sa.inspect(bind)

        def shape(name, schema):
            columns = inspector.get_columns(name, schema=schema)
            for column in columns:
                column["type"] = str(column["type"])
            foreign_keys = inspector.get_foreign_keys(name, schema=schema)
            for fk in foreign_keys:
                # References inside this schema or the fixed public run table.
                if fk["referred_schema"] not in (None, schema, "public"):
                    raise RuntimeError(
                        f"artifact provenance schema mismatch for {name}: foreign schema"
                    )
                fk["referred_schema"] = None
            result = [
                columns,
                inspector.get_pk_constraint(name, schema=schema),
                inspector.get_unique_constraints(name, schema=schema),
                inspector.get_check_constraints(name, schema=schema),
                foreign_keys,
                inspector.get_indexes(name, schema=schema),
            ]
            return json.dumps(result, sort_keys=True, default=str)

        for name in sorted(existing):
            if shape(name, "public") != shape(name, scratch):
                raise RuntimeError(f"artifact provenance schema mismatch for {name}")
            invalid = bind.scalar(
                sa.text("""SELECT count(*) FROM pg_constraint
                WHERE conrelid=CAST(:name AS regclass) AND (NOT convalidated OR condeferrable OR condeferred)"""),
                {"name": "public." + name},
            )
            if invalid:
                raise RuntimeError(
                    f"artifact provenance schema mismatch for {name}: invalid constraints"
                )
    finally:
        bind.execute(sa.text("SELECT set_config('search_path',:path,true)"), {"path": old_path})
        bind.execute(sa.text(f'DROP SCHEMA "{scratch}" CASCADE'))
