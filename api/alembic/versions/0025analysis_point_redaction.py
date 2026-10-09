"""Hide unavailable linked Run lineage in retained evaluation point rows.

The E11 summary snapshot intentionally retains all attempts. A later resource
revocation must not disclose those attempt identities through A01/A02 reads.
"""

import runpy
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0025analysis_point_redaction"
down_revision = "0024analysis_sealed_cache"
branch_labels = None
depends_on = None


_OLD_READ = """ SELECT COALESCE(jsonb_agg(CASE WHEN EXISTS(
   SELECT 1 FROM public.analysis_point_dependencies d LEFT JOIN LATERAL(SELECT x FROM jsonb_array_elements(checked->'rows')x WHERE x->>'run_id'=d.run_id::text)c ON true
   WHERE d.capture_kind=kind AND d.capture_id=capture AND d.scope_key=s AND d.result_id=r.result_id AND (NOT d.captured_available OR (c.x->>'available')::boolean IS DISTINCT FROM true OR c.x->>'manifest' IS DISTINCT FROM d.manifest))
  THEN jsonb_set(jsonb_set(r.body,'{row,subject_usage}','null'::jsonb),'{row,judge_usage}','null'::jsonb) ELSE r.body END ORDER BY r.id),'[]'::jsonb) INTO result
 FROM public.analysis_point_rows r WHERE r.capture_kind=kind AND r.capture_id=capture AND r.scope_key=s AND r.run_id=ANY(visible);"""


_NEW_READ = """ WITH current_state AS MATERIALIZED(
  SELECT x.run_id,x.available,x.manifest
  FROM jsonb_to_recordset(COALESCE(checked->'rows','[]'::jsonb))
   AS x(run_id uuid,available boolean,manifest text)),
 dependency_state AS MATERIALIZED(
  SELECT d.result_id,d.run_id,
   d.captured_available AND c.available IS TRUE
   AND c.manifest IS NOT DISTINCT FROM d.manifest AS available
  FROM public.analysis_point_dependencies d
  LEFT JOIN current_state c ON c.run_id=d.run_id
  WHERE d.capture_kind=kind AND d.capture_id=capture AND d.scope_key=s),
 result_state AS MATERIALIZED(
  SELECT d.result_id,bool_and(d.available) AS complete,
   COALESCE(array_agg(d.run_id) FILTER(WHERE d.available),ARRAY[]::uuid[]) AS allowed_runs
 FROM dependency_state d GROUP BY d.result_id)
 SELECT COALESCE(jsonb_agg(
  jsonb_set(r.body,'{row}',
   (r.body->'row')||
   CASE WHEN COALESCE(state.complete,true) THEN '{}'::jsonb
    ELSE jsonb_build_object('subject_usage',NULL,'judge_usage',NULL) END||
   jsonb_build_object(
    'attempts',COALESCE((SELECT jsonb_agg(attempt.value ORDER BY (attempt.value->>'attempt')::integer)
     FROM jsonb_array_elements(COALESCE(r.body->'row'->'attempts','[]'::jsonb)) attempt(value)
     WHERE (attempt.value->>'run_id')::uuid=ANY(state.allowed_runs)),'[]'::jsonb),
    'run_id',CASE WHEN (r.body->'row'->>'run_id')::uuid=ANY(state.allowed_runs)
      THEN r.body->'row'->'run_id' ELSE 'null'::jsonb END,
    'run_revision',CASE WHEN (r.body->'row'->>'run_id')::uuid=ANY(state.allowed_runs)
      THEN r.body->'row'->'run_revision' ELSE 'null'::jsonb END,
    'score_run_id',CASE WHEN (r.body->'row'->>'score_run_id')::uuid=ANY(state.allowed_runs)
      THEN r.body->'row'->'score_run_id' ELSE 'null'::jsonb END,
    'score_run_revision',CASE WHEN (r.body->'row'->>'score_run_id')::uuid=ANY(state.allowed_runs)
      THEN r.body->'row'->'score_run_revision' ELSE 'null'::jsonb END,
    'score_result_revision',CASE WHEN (r.body->'row'->>'score_run_id')::uuid=ANY(state.allowed_runs)
      THEN r.body->'row'->'score_result_revision' ELSE 'null'::jsonb END))
  ORDER BY r.id),'[]'::jsonb) INTO result
 FROM public.analysis_point_rows r
 LEFT JOIN result_state state ON state.result_id=r.result_id
 WHERE r.capture_kind=kind AND r.capture_id=capture AND r.scope_key=s AND r.run_id=ANY(visible);"""


def _points_sql() -> str:
    sql = runpy.run_path(str(Path(__file__).with_name("0022analysis_point_facts.py")))["FUNCTION"]
    signature = "CREATE FUNCTION public.opencitadel_analysis_points(encoded text,signature text) RETURNS jsonb"
    if sql.count(signature) != 1 or sql.count(_OLD_READ) != 1:
        raise RuntimeError("analysis point predecessor does not match redaction repair")
    return sql.replace(
        signature, signature.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION")
    ).replace(_OLD_READ, _NEW_READ)


def upgrade():
    bind = op.get_bind()
    api, kernel, owner = bind.execute(
        sa.text(
            "SELECT current_setting('app.runtime_database_role'),current_setting('app.execution_runtime_role'),current_user"
        )
    ).one()
    if not api or not kernel or api == kernel or owner in {api, kernel}:
        raise RuntimeError("distinct runtime roles required")
    quote = bind.dialect.identifier_preparer.quote
    bind.execute(sa.text(_points_sql()))
    function = "public.opencitadel_analysis_points(text,text)"
    bind.execute(
        sa.text(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC,{quote(api)},{quote(kernel)}")
    )
    bind.execute(sa.text(f"GRANT EXECUTE ON FUNCTION {function} TO {quote(api)},{quote(kernel)}"))


def downgrade():
    raise RuntimeError("analysis point lineage redaction downgrade is unsupported")
