"""Immutable configuration, rubric, suite and preflight evidence; no runtime admission."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0006evaluation_configuration"
down_revision = "0005evaluation_datasets"
branch_labels = None
depends_on = None

_SCOPE = """owner_user_id varchar(255), team_id varchar(255),
 scope_key varchar(261) GENERATED ALWAYS AS (CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END) STORED,
 created_by varchar(255) NOT NULL, created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP, schema_version integer NOT NULL DEFAULT 1 CHECK(schema_version=1),
 CHECK ((owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL))"""
_VERSION = f"""id uuid NOT NULL, entity_id uuid NOT NULL, revision integer NOT NULL CHECK(revision>0),
 name varchar(255) NOT NULL, body jsonb NOT NULL, fingerprint varchar(64) NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{{64}}$'), {_SCOPE},
 PRIMARY KEY(scope_key,id), UNIQUE(scope_key,entity_id,revision),
 FOREIGN KEY(scope_key,entity_id) REFERENCES evaluation_configuration_drafts(scope_key,id) ON DELETE RESTRICT"""
TABLES = {
    "evaluation_configuration_drafts": f"""id uuid NOT NULL, kind varchar(16) NOT NULL CHECK(kind IN ('config','rubric','suite')), name varchar(255) NOT NULL CHECK(length(trim(name))>0), revision integer NOT NULL CHECK(revision>0), definition jsonb NOT NULL, deleted boolean NOT NULL DEFAULT false, {_SCOPE}, PRIMARY KEY(scope_key,id)""",
    "evaluation_config_versions": _VERSION,
    "evaluation_rubric_versions": _VERSION
    + ", judge_config_version uuid NOT NULL, FOREIGN KEY(scope_key,judge_config_version) REFERENCES evaluation_config_versions(scope_key,id) ON DELETE RESTRICT",
    "evaluation_suite_versions": _VERSION
    + ", dataset_version uuid NOT NULL, rubric_version uuid NOT NULL, FOREIGN KEY(scope_key,dataset_version) REFERENCES evaluation_dataset_versions(scope_key,id) ON DELETE RESTRICT, FOREIGN KEY(scope_key,rubric_version) REFERENCES evaluation_rubric_versions(scope_key,id) ON DELETE RESTRICT",
    "evaluation_suite_configs": f"""suite_version uuid NOT NULL, config_version uuid NOT NULL, {_SCOPE}, PRIMARY KEY(scope_key,suite_version,config_version), FOREIGN KEY(scope_key,suite_version) REFERENCES evaluation_suite_versions(scope_key,id) ON DELETE RESTRICT, FOREIGN KEY(scope_key,config_version) REFERENCES evaluation_config_versions(scope_key,id) ON DELETE RESTRICT""",
    "evaluation_preflights": f"""id uuid NOT NULL, suite_version uuid NOT NULL, revision integer NOT NULL CHECK(revision>0), body jsonb NOT NULL, {_SCOPE}, PRIMARY KEY(scope_key,id), UNIQUE(scope_key,suite_version,revision), FOREIGN KEY(scope_key,suite_version) REFERENCES evaluation_suite_versions(scope_key,id) ON DELETE RESTRICT""",
}
INDEXES = [
    f"CREATE INDEX ix_{kind}_listing ON evaluation_{kind}_versions(scope_key,created_at,id)"
    for kind in ("config", "rubric", "suite")
]


def upgrade():
    upgrade_connection(op.get_bind())


def upgrade_connection(bind):
    from app.infrastructure.security.tenant_rls import policy_statements

    spec = importlib.util.spec_from_file_location(
        "e02_shape", Path(__file__).with_name("0002artifact_provenance.py")
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
            "CREATE OR REPLACE FUNCTION public.opencitadel_e02_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'immutable evaluation configuration fact'; END $$"
        )
    )
    for name, ddl in TABLES.items():
        if name not in existing:
            bind.execute(sa.text(f"CREATE TABLE {name} ({ddl})"))
        for statement in policy_statements(name):
            bind.execute(sa.text(statement))
        bind.execute(sa.text(f"REVOKE ALL ON {name} FROM PUBLIC,{quote(api)},{quote(kernel)}"))
        grants = (
            "SELECT,INSERT,UPDATE" if name == "evaluation_configuration_drafts" else "SELECT,INSERT"
        )
        bind.execute(sa.text(f"GRANT {grants} ON {name} TO {quote(api)},{quote(kernel)}"))
        if name != "evaluation_configuration_drafts":
            bind.execute(sa.text(f"DROP TRIGGER IF EXISTS e02_immutable ON {name}"))
            bind.execute(
                sa.text(
                    f"CREATE TRIGGER e02_immutable BEFORE UPDATE OR DELETE ON {name} FOR EACH ROW EXECUTE FUNCTION public.opencitadel_e02_immutable()"
                )
            )
    for statement in INDEXES:
        bind.execute(sa.text(statement.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ")))


def downgrade():
    raise RuntimeError("immutable configuration facts cannot be downgraded")
