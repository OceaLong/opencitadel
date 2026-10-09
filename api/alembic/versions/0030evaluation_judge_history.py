"""Expose only the exact judge Run UUID associated with a retained score set."""

import sqlalchemy as sa

from alembic import op

revision = "0030evaluation_judge_history"
down_revision = "0029analysis_team_sessions"
branch_labels = None
depends_on = None

FUNCTION = r"""
CREATE FUNCTION public.opencitadel_evaluation_score_judge(scope_value text,set_id_value uuid)
RETURNS uuid LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT j.run_id FROM public.evaluation_score_sets v
 JOIN public.evaluation_judge_intents j ON j.scope_key=v.scope_key
   AND v.source='model' AND v.request_id='judge:'||j.id::text
   AND j.batch_id=v.batch_id AND j.result_id=v.result_id
   AND j.rubric_id=v.rubric_revision
   AND j.candidate->>'run_id'=v.run_id::text
 WHERE v.scope_key=scope_value AND v.id=set_id_value
   AND public.opencitadel_authorization_valid()
   AND ((current_setting('app.auth_mode',true)='user' AND scope_value=
     CASE WHEN COALESCE(current_setting('app.team_id',true),'')<>''
       THEN 'team:'||current_setting('app.team_id',true)
       ELSE 'user:'||current_setting('app.user_id',true) END)
     OR (current_setting('app.auth_mode',true)='system'
       AND current_setting('app.system_actor',true)='execution-kernel'))
$$;
"""


def upgrade():
    bind = op.get_bind()
    api, kernel, owner = bind.execute(
        sa.text(
            "SELECT current_setting('app.runtime_database_role'),"
            "current_setting('app.execution_runtime_role'),current_user"
        )
    ).one()
    if not api or not kernel or api == kernel or owner in {api, kernel}:
        raise RuntimeError("distinct runtime roles required")
    quote = bind.dialect.identifier_preparer.quote
    bind.execute(sa.text(FUNCTION))
    signature = "public.opencitadel_evaluation_score_judge(text,uuid)"
    bind.execute(sa.text(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC"))
    bind.execute(sa.text(f"GRANT EXECUTE ON FUNCTION {signature} TO {quote(api)},{quote(kernel)}"))


def downgrade():
    op.execute("DROP FUNCTION public.opencitadel_evaluation_score_judge(text,uuid)")
