"""Scoped immutable test registries and durable, fenced isolation lifecycle."""

import importlib.util
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0008evaluation_environments"
down_revision = "0007evaluation_recordings"
branch_labels = None
depends_on = None
_SCOPE = """owner_user_id varchar(255), team_id varchar(255),
 scope_key varchar(261) GENERATED ALWAYS AS (CASE WHEN owner_user_id IS NOT NULL THEN 'user:' || owner_user_id ELSE 'team:' || team_id END) STORED,
 created_by varchar(255) NOT NULL, created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CHECK ((owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL))"""
TABLES = {
    "evaluation_environment_repairs": f"""id uuid NOT NULL, lease_id uuid NOT NULL, generation integer NOT NULL, lease_revision integer NOT NULL, principal jsonb NOT NULL, status varchar(16) NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','applied','rejected')), {_SCOPE}, PRIMARY KEY(scope_key,id)""",
    "evaluation_environment_registry": f"""id uuid NOT NULL, kind varchar(16) NOT NULL CHECK(kind IN ('target','credential','environment')), revision integer NOT NULL CHECK(revision>0), body jsonb NOT NULL, digest varchar(64) NOT NULL, {_SCOPE}, PRIMARY KEY(scope_key,kind,id,revision)""",
    # Global uniqueness rejects a physical resource registered in another workspace, including aliases.
    "evaluation_environment_resources": f"""physical_resource varchar(255) PRIMARY KEY, origin varchar(2048) NOT NULL UNIQUE, {_SCOPE}""",
    "evaluation_environment_leases": f"""id uuid NOT NULL, environment_version uuid NOT NULL, generation integer NOT NULL CHECK(generation>0), revision integer NOT NULL CHECK(revision>0), state varchar(16) NOT NULL CHECK(state IN ('allocated','preparing','ready','leased','cleaning','verified_clean','quarantine')), namespace varchar(64) NOT NULL UNIQUE, case_slot jsonb NOT NULL, requester jsonb NOT NULL DEFAULT '{{}}'::jsonb, expires_at timestamptz NOT NULL, repair_authorized boolean NOT NULL DEFAULT false, resources jsonb NOT NULL DEFAULT '[]'::jsonb, actual_versions jsonb NOT NULL DEFAULT '{{}}'::jsonb, error varchar(128), {_SCOPE}, PRIMARY KEY(scope_key,id), UNIQUE(scope_key,case_slot,generation)""",
    "evaluation_environment_fences": f"""physical_resource varchar(255) PRIMARY KEY REFERENCES evaluation_environment_resources(physical_resource), lease_id uuid, generation integer, {_SCOPE}, FOREIGN KEY(scope_key,lease_id) REFERENCES evaluation_environment_leases(scope_key,id), CHECK((lease_id IS NULL)=(generation IS NULL))""",
    "evaluation_environment_operations": f"""id uuid NOT NULL, lease_id uuid NOT NULL, generation integer NOT NULL, lease_revision integer NOT NULL, phase varchar(16) NOT NULL CHECK(phase IN ('prepare','reset','verify_ready','cleanup','verify_clean')), status varchar(16) NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','done','failed','superseded')), claim_generation integer NOT NULL DEFAULT 0, claim_until timestamptz, receipt jsonb, error varchar(128), {_SCOPE}, PRIMARY KEY(scope_key,id), UNIQUE(scope_key,lease_id,generation,lease_revision,phase), FOREIGN KEY(scope_key,lease_id) REFERENCES evaluation_environment_leases(scope_key,id)""",
    "evaluation_environment_bindings": f"""run_id uuid NOT NULL, lease_id uuid NOT NULL, generation integer NOT NULL, principal jsonb NOT NULL, admission jsonb NOT NULL, actual_versions jsonb NOT NULL, {_SCOPE}, PRIMARY KEY(scope_key,run_id), FOREIGN KEY(scope_key,lease_id) REFERENCES evaluation_environment_leases(scope_key,id)""",
}
INDEXES = [
    "CREATE INDEX ix_environment_operations_claim ON evaluation_environment_operations(status,claim_until,created_at)",
    "CREATE INDEX ix_environment_leases_expiry ON evaluation_environment_leases(state,expires_at)",
]
REGISTRY = {"evaluation_environment_registry", "evaluation_environment_resources"}
ADMIN_INSERT = REGISTRY | {"evaluation_environment_repairs"}
IMMUTABLE = REGISTRY | {"evaluation_environment_bindings"}


def upgrade():
    upgrade_connection(op.get_bind())


def upgrade_connection(bind):
    spec = importlib.util.spec_from_file_location(
        "e04_shape", Path(__file__).with_name("0002artifact_provenance.py")
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
    system = "current_setting('app.auth_mode',true)='system'"
    scoped = "(current_setting('app.auth_mode',true)='user' AND ((team_id IS NOT NULL AND team_id=NULLIF(current_setting('app.team_id',true),'')) OR (team_id IS NULL AND owner_user_id=NULLIF(current_setting('app.user_id',true),''))))"
    admin = "current_setting('app.is_admin',true)='true'"
    bind.execute(
        sa.text(
            "CREATE OR REPLACE FUNCTION public.opencitadel_e04_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'immutable environment fact'; END $$"
        )
    )
    for name, ddl in TABLES.items():
        if name not in existing:
            bind.execute(sa.text(f"CREATE TABLE {name} ({ddl})"))
        bind.execute(sa.text(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY"))
        for command in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            pname = "e04_" + command.lower()
            bind.execute(sa.text(f"DROP POLICY IF EXISTS {pname} ON {name}"))
            predicate = f"{valid} AND ({system} OR {scoped})"
            if command != "SELECT" and name in ADMIN_INSERT:
                predicate = f"{valid} AND ({system} OR ({scoped} AND {admin}))"
            clause = f"WITH CHECK ({predicate})" if command == "INSERT" else f"USING ({predicate})"
            bind.execute(sa.text(f"CREATE POLICY {pname} ON {name} FOR {command} {clause}"))
        bind.execute(sa.text(f"REVOKE ALL ON {name} FROM PUBLIC,{quote(api)},{quote(kernel)}"))
        grants = "SELECT,INSERT" if name in IMMUTABLE else "SELECT,INSERT,UPDATE"
        bind.execute(sa.text(f"GRANT {grants} ON {name} TO {quote(kernel)}"))
        bind.execute(
            sa.text(
                f"GRANT {'SELECT,INSERT' if name in ADMIN_INSERT else 'SELECT'} ON {name} TO {quote(api)}"
            )
        )
        if name in IMMUTABLE:
            bind.execute(sa.text(f"DROP TRIGGER IF EXISTS e04_immutable ON {name}"))
            bind.execute(
                sa.text(
                    f"CREATE TRIGGER e04_immutable BEFORE UPDATE OR DELETE ON {name} FOR EACH ROW EXECUTE FUNCTION public.opencitadel_e04_immutable()"
                )
            )
    for statement in INDEXES:
        bind.execute(sa.text(statement.replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ")))


def downgrade():
    raise RuntimeError("environment facts cannot be downgraded")
