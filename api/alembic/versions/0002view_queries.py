"""Independent view generations and expiring scoped pagination cohorts.

Fixed forward DDL: journal identity and frozen prior revisions are unchanged.
"""

import sqlalchemy as sa

from alembic import op

revision = "0002view_queries"
down_revision = "0002execution_view"
branch_labels = None
depends_on = None

_SCOPE = """
owner_user_id varchar(255), team_id varchar(255),
scope_key varchar(261) GENERATED ALWAYS AS
(CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END) STORED,
created_by varchar(255) NOT NULL,
created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
schema_version integer NOT NULL DEFAULT 1 CHECK (schema_version > 0),
CHECK ((owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL))
"""


def upgrade():
    upgrade_connection(op.get_bind())


def upgrade_connection(bind):
    from app.infrastructure.security.tenant_rls import policy_statements

    tables = {
        "execution_view_generations": f"""generation uuid PRIMARY KEY, algorithm_version integer NOT NULL CHECK (algorithm_version > 0),
            source_version integer NOT NULL CHECK (source_version > 0), status varchar(20) NOT NULL CHECK (status IN ('building','active','retired','failed')),
            captured_runs bigint NOT NULL DEFAULT 0, {_SCOPE}, UNIQUE(generation,scope_key)""",
        "execution_view_controls": f"""active_generation uuid, {_SCOPE}, PRIMARY KEY(scope_key),
            FOREIGN KEY(active_generation,scope_key) REFERENCES execution_view_generations(generation,scope_key)""",
        "execution_view_shadow_runs": f"""generation uuid NOT NULL, run_id uuid NOT NULL, observed_order bigint NOT NULL CHECK(observed_order >= 0),
            boundary jsonb NOT NULL, state jsonb NOT NULL, missing_intervals jsonb NOT NULL, {_SCOPE},
            PRIMARY KEY(generation,run_id), UNIQUE(generation,run_id,scope_key),
            FOREIGN KEY(generation,scope_key) REFERENCES execution_view_generations(generation,scope_key),
            FOREIGN KEY(run_id,scope_key) REFERENCES execution_view_runs(run_id,scope_key), coverage_token text""",
        "execution_view_shadow_steps": f"""generation uuid NOT NULL, run_id uuid NOT NULL, step_id varchar(255) NOT NULL,
            observed_order bigint NOT NULL CHECK(observed_order >= 0), payload jsonb NOT NULL, {_SCOPE},
            PRIMARY KEY(generation,run_id,step_id),
            FOREIGN KEY(generation,run_id,scope_key) REFERENCES execution_view_shadow_runs(generation,run_id,scope_key) ON DELETE CASCADE""",
        "execution_view_cohorts": f"""cohort_id uuid PRIMARY KEY, generation varchar(36) NOT NULL,
            expires_at timestamptz NOT NULL, {_SCOPE}, UNIQUE(cohort_id,scope_key),
            CHECK(expires_at > created_at AND expires_at <= created_at + interval '15 minutes')""",
        "execution_view_cohort_runs": f"""cohort_id uuid NOT NULL, run_id uuid NOT NULL, admitted_at timestamptz,
            boundary jsonb NOT NULL, {_SCOPE}, PRIMARY KEY(cohort_id,run_id),
            FOREIGN KEY(cohort_id,scope_key) REFERENCES execution_view_cohorts(cohort_id,scope_key) ON DELETE CASCADE,
            FOREIGN KEY(run_id,scope_key) REFERENCES execution_view_runs(run_id,scope_key)""",
    }
    tables[
        "execution_view_read_cuts"
    ] = f"""cut_id uuid PRIMARY KEY, run_id uuid NOT NULL, generation varchar(36) NOT NULL,
        boundary jsonb NOT NULL, state jsonb NOT NULL, missing_intervals jsonb NOT NULL, coverage_token text NOT NULL,
        expires_at timestamptz NOT NULL, {_SCOPE}, UNIQUE(cut_id,scope_key),
        FOREIGN KEY(run_id,scope_key) REFERENCES execution_view_runs(run_id,scope_key),
        CHECK(expires_at > created_at AND expires_at <= created_at + interval '15 minutes')"""
    tables[
        "execution_view_read_steps"
    ] = f"""cut_id uuid NOT NULL, step_id varchar(255) NOT NULL, observed_order bigint NOT NULL,
        payload jsonb NOT NULL, {_SCOPE}, PRIMARY KEY(cut_id,step_id),
        FOREIGN KEY(cut_id,scope_key) REFERENCES execution_view_read_cuts(cut_id,scope_key) ON DELETE CASCADE"""
    indexes = [
        "CREATE INDEX ix_view_read_cut_expiry ON execution_view_read_cuts(expires_at)",
        "CREATE INDEX ix_view_read_step_page ON execution_view_read_steps(scope_key,cut_id,observed_order DESC,step_id DESC)",
        "CREATE INDEX ix_view_cohort_expiry ON execution_view_cohorts(expires_at)",
        "CREATE INDEX ix_view_cohort_page ON execution_view_cohort_runs(scope_key,cohort_id,admitted_at DESC NULLS LAST,run_id DESC)",
        "CREATE INDEX ix_view_shadow_scope ON execution_view_shadow_runs(scope_key,generation,run_id)",
        "CREATE INDEX ix_view_shadow_step_page ON execution_view_shadow_steps(scope_key,generation,run_id,observed_order DESC,step_id DESC)",
    ]
    existing = set(sa.inspect(bind).get_table_names(schema="public")) & tables.keys()
    if existing and "coverage_token" not in {
        c["name"] for c in sa.inspect(bind).get_columns("execution_view_shadow_runs")
    }:
        # Additive transition from the first, not-yet-approved F04 schema.
        old_tables = {
            k: v.replace(", coverage_token text", "")
            for k, v in tables.items()
            if k not in ("execution_view_read_cuts", "execution_view_read_steps")
        }
        old_indexes = [i for i in indexes if "ix_view_read_" not in i]
        _validate_existing(bind, old_tables, old_indexes, existing)
        bind.execute(
            sa.text("ALTER TABLE execution_view_shadow_runs ADD COLUMN coverage_token text")
        )
    if existing:
        _validate_existing(bind, tables, indexes, existing)
    api, kernel = bind.execute(
        sa.text(
            "SELECT current_setting('app.runtime_database_role'),current_setting('app.execution_runtime_role')"
        )
    ).one()
    if not api or not kernel or api == kernel:
        raise RuntimeError("distinct runtime roles required")
    quote = bind.dialect.identifier_preparer.quote
    for name, columns in tables.items():
        if name not in existing:
            bind.execute(sa.text(f"CREATE TABLE {name} ({columns})"))
        for statement in policy_statements(name):
            bind.execute(sa.text(statement))
        bind.execute(sa.text(f"REVOKE ALL ON {name} FROM PUBLIC, {quote(api)}, {quote(kernel)}"))
        bind.execute(sa.text(f"GRANT SELECT ON {name} TO {quote(api)}"))
        bind.execute(sa.text(f"GRANT SELECT,INSERT,UPDATE,DELETE ON {name} TO {quote(kernel)}"))
        if name in (
            "execution_view_cohorts",
            "execution_view_cohort_runs",
            "execution_view_read_cuts",
            "execution_view_read_steps",
        ):
            bind.execute(sa.text(f"GRANT INSERT ON {name} TO {quote(api)}"))
    for statement in indexes:
        bind.execute(sa.text(statement.replace("CREATE INDEX", "CREATE INDEX IF NOT EXISTS")))
    # Match SQLAlchemy JSONB subscript spelling and the effective-source predicate.
    bind.execute(
        sa.text("""CREATE INDEX IF NOT EXISTS ix_view_progress_latest_effective ON execution_view_observations
        (run_id,projector_version,((public_payload['source'])->>'activity_id'),observed_order DESC)
        WHERE source_kind='progress' AND coalesce((public_payload->>'applied')::boolean,true)""")
    )


def _validate_existing(bind, tables, indexes, existing):
    """Compare PostgreSQL-normalized fixed DDL before touching precreated tables."""
    import json
    from uuid import uuid4

    scratch = "view_query_shape_" + uuid4().hex
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
                    raise RuntimeError(f"view query schema mismatch for {name}: foreign schema")
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
                raise RuntimeError(f"view query schema mismatch for {name}")
            invalid = bind.scalar(
                sa.text("""SELECT count(*) FROM pg_constraint
                WHERE conrelid=CAST(:name AS regclass) AND (NOT convalidated OR condeferrable OR condeferred)"""),
                {"name": "public." + name},
            )
            if invalid:
                raise RuntimeError(f"view query schema mismatch for {name}: invalid constraints")
    finally:
        bind.execute(sa.text("SELECT set_config('search_path',:path,true)"), {"path": old_path})
        bind.execute(sa.text(f'DROP SCHEMA "{scratch}" CASCADE'))


def downgrade():
    raise RuntimeError(
        "view generation downgrade requires explicit cursor retirement and backup restore"
    )
