"""Keep linked evaluation point bindings inside the redacted-resource boundary.

0026 repaired analysis manifests and comparison materialization, but the
independent point-bindings function still contained the older resource query.
"""

import runpy
from pathlib import Path

import sqlalchemy as sa

from alembic import op

revision = "0027analysis_point_redaction"
down_revision = "0026analysis_redacted_inputs"
branch_labels = None
depends_on = None


def _point_bindings_sql() -> str:
    directory = Path(__file__).parent
    previous = runpy.run_path(str(directory / "0017execution_comparisons.py"))["RESOURCE_BINDINGS"]
    redaction = runpy.run_path(str(directory / "0026analysis_redacted_inputs.py"))
    bindings = redaction["_replace_once"](
        previous, redaction["_CONTENT_JOIN"], redaction["_CONTENT_FILTER"]
    )
    bindings = redaction["_filter_recording_pins"](bindings)
    # A TABLE-returning PL/pgSQL function has an OUT variable named run_id;
    # qualify the historical JSON record column instead of relying on name
    # resolution that is only safe in the inlined comparison function.
    bindings = redaction["_replace_once"](
        bindings,
        "UNION SELECT run_id,kind,id,version,true AS pinned FROM "
        "jsonb_to_recordset(CAST(resources_json AS jsonb)) "
        "AS j(run_id uuid,kind text,id text,version text)",
        "UNION SELECT j.run_id,j.kind,j.id,j.version,true AS pinned FROM "
        "jsonb_to_recordset(CAST(resources_json AS jsonb)) "
        "AS j(run_id uuid,kind text,id text,version text)",
    )
    return (
        "CREATE OR REPLACE FUNCTION public.opencitadel_analysis_point_bindings"
        "(scope_value text,capture_id_value uuid,members_json jsonb,resources_json jsonb)"
        " RETURNS TABLE(run_id uuid,source jsonb,resources jsonb,pins jsonb,owners jsonb)"
        " LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS $$"
        " DECLARE owner_value text;team_value text;"
        " BEGIN team_value:=NULLIF(current_setting('app.team_id',true),'');"
        " owner_value:=CASE WHEN team_value IS NULL THEN current_setting('app.user_id',true)"
        " ELSE NULL END; RETURN QUERY " + bindings + "; END $$"
    )


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
    bind.execute(sa.text(_point_bindings_sql()))
    quote = bind.dialect.identifier_preparer.quote
    bind.execute(
        sa.text(
            "REVOKE ALL ON FUNCTION public.opencitadel_analysis_point_bindings"
            "(text,uuid,jsonb,jsonb) FROM PUBLIC,"
            f"{quote(api)},{quote(kernel)}"
        )
    )


def downgrade():
    raise RuntimeError("redacted analysis point bindings downgrade is unsupported")
