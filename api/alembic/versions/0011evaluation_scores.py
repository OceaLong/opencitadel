"""Append-only, source-separated evaluation revisions; no execution schema rewrite."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0011evaluation_scores"
down_revision = "0010evaluation_batches"
branch_labels = None
depends_on = None

_SCOPE = """owner_user_id varchar(255), team_id varchar(255),
 scope_key varchar(261) GENERATED ALWAYS AS (CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END) STORED,
 created_by varchar(255) NOT NULL, created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK ((owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL))"""
TABLES = {
    "evaluation_score_heads": f"""batch_id uuid NOT NULL, revision bigint NOT NULL CHECK(revision>=0),
      {_SCOPE}, PRIMARY KEY(scope_key,batch_id), FOREIGN KEY(scope_key,batch_id) REFERENCES evaluation_batches(scope_key,id) ON DELETE RESTRICT""",
    "evaluation_score_sets": f"""id uuid NOT NULL, batch_id uuid NOT NULL, result_id uuid NOT NULL,
      result_revision bigint NOT NULL CHECK(result_revision>0), run_id uuid NOT NULL, run_revision bigint NOT NULL CHECK(run_revision>0),
      evaluation_revision bigint NOT NULL CHECK(evaluation_revision>0), rubric_revision uuid NOT NULL,
      source varchar(8) NOT NULL CHECK(source IN ('rule','model','human')), request_id varchar(255) NOT NULL,
      fingerprint varchar(64) NOT NULL, status varchar(16) NOT NULL CHECK(status IN ('complete','skipped','failed','not_required')),
      required_dimensions jsonb NOT NULL, applicable_dimensions jsonb NOT NULL,
      {_SCOPE}, PRIMARY KEY(scope_key,id), UNIQUE(scope_key,batch_id,evaluation_revision), UNIQUE(scope_key,result_id,source,request_id),
      FOREIGN KEY(scope_key,batch_id) REFERENCES evaluation_batches(scope_key,id) ON DELETE RESTRICT,
      FOREIGN KEY(scope_key,result_id) REFERENCES evaluation_batch_results(scope_key,id) ON DELETE RESTRICT,
      FOREIGN KEY(scope_key,rubric_revision) REFERENCES evaluation_rubric_versions(scope_key,id) ON DELETE RESTRICT""",
    "evaluation_scores": f"""id uuid NOT NULL, set_id uuid NOT NULL, dimension varchar(255) NOT NULL,
      status varchar(16) NOT NULL CHECK(status IN ('valid','not_evaluable','error')), value jsonb,
      reason varchar(2000) NOT NULL, evidence jsonb NOT NULL, recording jsonb, supersedes_id uuid,
      {_SCOPE}, PRIMARY KEY(scope_key,id), UNIQUE(scope_key,set_id,dimension),
      FOREIGN KEY(scope_key,set_id) REFERENCES evaluation_score_sets(scope_key,id) ON DELETE RESTRICT,
      FOREIGN KEY(scope_key,supersedes_id) REFERENCES evaluation_scores(scope_key,id) ON DELETE RESTRICT,
      CHECK((status='valid' AND value IS NOT NULL AND value!='null'::jsonb) OR (status!='valid' AND value IS NULL))""",
}
INDEXES = [
    "CREATE INDEX ix_evaluation_score_sets_source ON evaluation_score_sets(scope_key,result_id,source,evaluation_revision DESC)"
]


def upgrade():
    upgrade_connection(op.get_bind())


def upgrade_connection(bind):
    spec = importlib.util.spec_from_file_location(
        "e07_shape", Path(__file__).with_name("0002artifact_provenance.py")
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
    valid = "opencitadel_authorization_valid()"
    system = "current_setting('app.auth_mode',true)='system' AND current_setting('app.system_actor',true)='execution-kernel'"
    scoped = "current_setting('app.auth_mode',true)='user' AND ((team_id IS NOT NULL AND team_id=NULLIF(current_setting('app.team_id',true),'')) OR (team_id IS NULL AND owner_user_id=NULLIF(current_setting('app.user_id',true),'')))"
    for name, ddl in TABLES.items():
        if name not in existing:
            bind.execute(sa.text(f"CREATE TABLE {name} ({ddl})"))
        bind.execute(sa.text(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY"))
        bind.execute(
            sa.text(
                f"CREATE POLICY e07_select ON {name} FOR SELECT USING ({valid} AND ({system} OR ({scoped})))"
            )
        )
        bind.execute(
            sa.text(
                f"CREATE POLICY e07_insert ON {name} FOR INSERT WITH CHECK ({valid} AND ({system}))"
            )
        )
        bind.execute(sa.text(f"REVOKE ALL ON {name} FROM PUBLIC,{quote(api)},{quote(kernel)}"))
        bind.execute(sa.text(f"GRANT SELECT ON {name} TO {quote(api)}"))
        bind.execute(sa.text(f"GRANT SELECT,INSERT ON {name} TO {quote(kernel)}"))
    bind.execute(
        sa.text(
            f"CREATE POLICY e07_update ON evaluation_score_heads FOR UPDATE USING ({valid} AND ({system})) WITH CHECK ({valid} AND ({system}))"
        )
    )
    bind.execute(sa.text(f"GRANT UPDATE(revision) ON evaluation_score_heads TO {quote(kernel)}"))
    for ddl in INDEXES:
        bind.execute(sa.text(ddl.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1)))
    bind.execute(
        sa.text("""
CREATE FUNCTION public.opencitadel_e07_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
BEGIN RAISE EXCEPTION 'evaluation_score_history_immutable'; END $$
""")
    )
    for name in ("evaluation_scores", "evaluation_score_sets"):
        bind.execute(
            sa.text(
                f"CREATE TRIGGER e07_immutable BEFORE UPDATE OR DELETE ON {name} FOR EACH ROW EXECUTE FUNCTION public.opencitadel_e07_immutable()"
            )
        )
    bind.execute(
        sa.text("""
CREATE FUNCTION public.opencitadel_e07_score_value() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog AS $$
DECLARE source_kind text;
BEGIN
    SELECT source INTO source_kind FROM public.evaluation_score_sets
      WHERE scope_key=CASE WHEN NEW.owner_user_id IS NOT NULL THEN 'user:' || NEW.owner_user_id ELSE 'team:' || NEW.team_id END AND id=NEW.set_id;
    IF source_kind IS NULL THEN RAISE EXCEPTION 'score_set_unavailable'; END IF;
    IF NEW.status='valid' AND (
       (source_kind='rule' AND jsonb_typeof(NEW.value) IS DISTINCT FROM 'boolean')
       OR (source_kind!='rule' AND (jsonb_typeof(NEW.value) IS DISTINCT FROM 'number' OR NEW.value::text !~ '^[0-4]$'))
    ) THEN RAISE EXCEPTION 'score_value_invalid'; END IF;
    RETURN NEW;
END $$
""")
    )
    bind.execute(
        sa.text(
            "CREATE TRIGGER e07_score_value BEFORE INSERT ON evaluation_scores FOR EACH ROW EXECUTE FUNCTION public.opencitadel_e07_score_value()"
        )
    )


def downgrade():
    raise RuntimeError("evaluation score history cannot be downgraded")
