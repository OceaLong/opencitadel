"""Durable restricted judge protocol and explicit additional rescore authorizations."""

import sqlalchemy as sa

from alembic import op

revision = "0012evaluation_judges"
down_revision = "0011evaluation_scores"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    api, kernel = bind.execute(
        sa.text(
            "SELECT current_setting('app.runtime_database_role'),current_setting('app.execution_runtime_role')"
        )
    ).one()
    if not api or not kernel or api == kernel:
        raise RuntimeError("distinct runtime roles required")
    quote = bind.dialect.identifier_preparer.quote
    bind.execute(
        sa.text("""CREATE TABLE evaluation_judge_intents (
      id uuid NOT NULL, run_id uuid NOT NULL, batch_id uuid NOT NULL, result_id uuid NOT NULL,
      namespace_id uuid NOT NULL, rubric_id uuid NOT NULL, config_id uuid NOT NULL,
      protocol integer NOT NULL CHECK(protocol=1), candidate jsonb NOT NULL,
      materials jsonb NOT NULL, request_id varchar(255) NOT NULL, fingerprint varchar(64) NOT NULL,
      rescore jsonb, authorizer jsonb, CHECK ((rescore IS NULL) = (authorizer IS NULL)), owner_user_id varchar(255), team_id varchar(255),
      scope_key varchar(261) GENERATED ALWAYS AS (CASE WHEN owner_user_id IS NOT NULL THEN 'user:'||owner_user_id ELSE 'team:'||team_id END) STORED,
      created_by varchar(255) NOT NULL, created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(scope_key,id), UNIQUE(scope_key,run_id), UNIQUE(scope_key,result_id,request_id),
      FOREIGN KEY(scope_key,batch_id) REFERENCES evaluation_batches(scope_key,id) ON DELETE RESTRICT,
      FOREIGN KEY(scope_key,result_id) REFERENCES evaluation_batch_results(scope_key,id) ON DELETE RESTRICT,
      FOREIGN KEY(scope_key,rubric_id) REFERENCES evaluation_rubric_versions(scope_key,id) ON DELETE RESTRICT,
      FOREIGN KEY(scope_key,config_id) REFERENCES evaluation_config_versions(scope_key,id) ON DELETE RESTRICT,
      CHECK ((owner_user_id IS NOT NULL AND team_id IS NULL) OR (owner_user_id IS NULL AND team_id IS NOT NULL)))""")
    )
    bind.execute(
        sa.text(
            "CREATE UNIQUE INDEX ix_judge_initial ON evaluation_judge_intents(scope_key,result_id) WHERE rescore IS NULL"
        )
    )
    bind.execute(
        sa.text("""CREATE TABLE evaluation_judge_work (
      scope_key varchar(261) NOT NULL, intent_id uuid NOT NULL,
      status varchar(20) NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','submitted','settled','stopped')),
      envelope jsonb, cancel_envelope jsonb, checked_at timestamptz NOT NULL DEFAULT '-infinity', evaluation_revision bigint, error varchar(100),
      PRIMARY KEY(scope_key,intent_id), FOREIGN KEY(scope_key,intent_id) REFERENCES evaluation_judge_intents(scope_key,id) ON DELETE RESTRICT)""")
    )
    bind.execute(
        sa.text("""CREATE TABLE evaluation_judge_invalidations (
      scope_key varchar(261) NOT NULL,intent_id uuid NOT NULL,batch_id uuid NOT NULL,
      run_id uuid NOT NULL,source_set_id uuid,evaluation_revision bigint NOT NULL CHECK(evaluation_revision>0),
      observed_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY(scope_key,intent_id),UNIQUE(scope_key,batch_id,evaluation_revision),
      FOREIGN KEY(scope_key,intent_id) REFERENCES evaluation_judge_intents(scope_key,id) ON DELETE RESTRICT,
      FOREIGN KEY(scope_key,source_set_id) REFERENCES evaluation_score_sets(scope_key,id) ON DELETE RESTRICT)""")
    )
    valid = "opencitadel_authorization_valid() AND current_setting('app.auth_mode',true)='system' AND current_setting('app.system_actor',true)='execution-kernel'"
    for table in (
        "evaluation_judge_intents",
        "evaluation_judge_work",
        "evaluation_judge_invalidations",
    ):
        bind.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        bind.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        bind.execute(
            sa.text(
                f"CREATE POLICY e08_kernel ON {table} FOR ALL USING ({valid}) WITH CHECK ({valid})"
            )
        )
        bind.execute(sa.text(f"REVOKE ALL ON {table} FROM PUBLIC,{quote(api)},{quote(kernel)}"))
        bind.execute(sa.text(f"GRANT SELECT,INSERT ON {table} TO {quote(kernel)}"))
    bind.execute(
        sa.text(
            f"GRANT UPDATE(status,envelope,cancel_envelope,checked_at,evaluation_revision,error) ON evaluation_judge_work TO {quote(kernel)}"
        )
    )
    bind.execute(
        sa.text(
            "CREATE TRIGGER immutable_judge_invalidation BEFORE UPDATE OR DELETE ON evaluation_judge_invalidations FOR EACH ROW EXECUTE FUNCTION opencitadel_e07_immutable()"
        )
    )
    bind.execute(
        sa.text("""CREATE FUNCTION opencitadel_e08_work_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN
      IF (OLD.scope_key,OLD.intent_id) IS DISTINCT FROM (NEW.scope_key,NEW.intent_id)
        OR (OLD.envelope IS NOT NULL AND OLD.envelope IS DISTINCT FROM NEW.envelope)
        OR (OLD.cancel_envelope IS NOT NULL AND OLD.cancel_envelope IS DISTINCT FROM NEW.cancel_envelope)
        OR (OLD.evaluation_revision IS NOT NULL AND OLD.evaluation_revision IS DISTINCT FROM NEW.evaluation_revision)
        OR (OLD.status IN ('settled','stopped') AND OLD.status IS DISTINCT FROM NEW.status)
        OR (OLD.status='submitted' AND NEW.status='pending') THEN RAISE EXCEPTION 'judge_work_identity_immutable'; END IF;
      RETURN NEW; END $$""")
    )
    bind.execute(
        sa.text(
            "CREATE TRIGGER immutable_judge_work BEFORE UPDATE ON evaluation_judge_work FOR EACH ROW EXECUTE FUNCTION opencitadel_e08_work_guard()"
        )
    )
    bind.execute(
        sa.text(
            "CREATE TRIGGER immutable_judge_intent BEFORE UPDATE OR DELETE ON evaluation_judge_intents FOR EACH ROW EXECUTE FUNCTION opencitadel_e07_immutable()"
        )
    )


def downgrade():
    raise RuntimeError("durable judge history cannot be downgraded")
