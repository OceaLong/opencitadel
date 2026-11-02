"""Expose narrow, signed-scope intent eligibility and approval digest reads."""

import sqlalchemy as sa

from alembic import op

revision = "0035tool_intent_reads"
down_revision = "0034execution_effect_operations"
branch_labels = None
depends_on = None

AUTH = """
public.opencitadel_authorization_valid()
AND ((current_setting('app.auth_mode',true)='user' AND scope_value=
  CASE WHEN COALESCE(current_setting('app.team_id',true),'')<>''
    THEN 'team:'||current_setting('app.team_id',true)
    ELSE 'user:'||current_setting('app.user_id',true) END)
  OR (current_setting('app.auth_mode',true)='system'
    AND current_setting('app.system_actor',true)='execution-kernel'))
"""

FUNCTIONS = {
    "public.opencitadel_tool_intent_state(text,uuid,uuid)": f"""
CREATE FUNCTION public.opencitadel_tool_intent_state(scope_value text,run_value uuid,activity_value uuid)
RETURNS TABLE(status text,failure_code text,subject_digest text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT t.status::text,t.failure_code::text,
   COALESCE(e.public_payload->>'subject_digest', (
     SELECT r.public_payload->>'expected_subject_digest' FROM public.execution_events r
     WHERE r.owner_scope_key=scope_value AND r.stream_type='run' AND r.stream_id=run_value::text
       AND r.event_type='ToolIntentRefreshRequested'
       AND r.public_payload->>'prepare_activity_id'=activity_value::text
       AND r.public_payload->>'generation'=t.request_generation::text
     ORDER BY r.stream_version DESC LIMIT 1
   ))
 FROM public.execution_activity_tasks t
 JOIN public.execution_events e ON e.position=t.request_event_position
 WHERE t.aggregate_type='run' AND t.aggregate_id=run_value::text AND t.activity_id=activity_value
   AND e.owner_scope_key=scope_value AND e.stream_type='run' AND e.stream_id=run_value::text
   AND {AUTH}
$$;
""",
    "public.opencitadel_approval_subject(text,uuid,uuid)": f"""
CREATE FUNCTION public.opencitadel_approval_subject(scope_value text,run_value uuid,approval_value uuid)
RETURNS TABLE(subject_digest text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog AS $$
 SELECT e.public_payload->>'subject_digest' FROM public.execution_events e
 WHERE e.owner_scope_key=scope_value AND e.stream_type='run' AND e.stream_id=run_value::text
   AND e.event_type='ApprovalRequested' AND e.public_payload->>'approval_id'=approval_value::text
   AND {AUTH}
 ORDER BY e.stream_version DESC LIMIT 1
$$;
""",
}


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
    for signature, ddl in FUNCTIONS.items():
        bind.execute(sa.text(ddl))
        bind.execute(sa.text(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC"))
        bind.execute(
            sa.text(f"GRANT EXECUTE ON FUNCTION {signature} TO {quote(api)},{quote(kernel)}")
        )


def downgrade():
    for signature in reversed(FUNCTIONS):
        op.execute(f"DROP FUNCTION {signature}")
